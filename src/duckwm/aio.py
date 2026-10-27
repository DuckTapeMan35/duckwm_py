"""The client on asyncio: concurrent requests, async callbacks.

    async def main():
        async with await aio.Client.connect() as c:
            async def on_key():
                print(await c.get_focused())  # inside the key press
                return cmd.kill_client()
            await c.bind("super+u", on_key)
            await c.default_arranger(layouts.tile)
            await c.closed()

    asyncio.run(main())

duckwm handles one transaction at a time. While it waits on a callback, only
that callback's requests (and finally its reply) may go out; they run inside
its transaction, and everything else waits. Callbacks nest: a callback's
request can make duckwm call another one, which then has the floor until it
returns. Which callback a request belongs to follows the task it's made in
(a context variable), so callbacks use the same Client as everything else, and
tasks they start belong to them too while they last.
"""

import asyncio
import contextlib
import contextvars
import functools
import inspect
import logging
import socket
import struct
import sys
import traceback
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Self

from . import cmd
from ._aio_api import AsyncApi
from ._encode import CallbackLike, Cmd, CommandLike, encode_all
from ._mods import KeyCombo
from ._proto import duckwm_pb2 as pb
from ._reply import UNSET, Reply
from ._wire import MAX_FRAME, Closed, socket_path
from .arrange import Arranger, Snapshot
from .client import (
    Rejected,
    Workspace,
    _as_reply,
    first_connection,
    platform_of,
    scope_path,
)
from .value import from_value, to_value

Handler = Callable[[pb.CallArgs], Awaitable[Reply]]

# The call being answered in this task: 0 at the top level.
_scope: contextvars.ContextVar[int] = contextvars.ContextVar("duckwm_scope", default=0)


@dataclass
class _Request:
    scope: int
    commands: list[pb.Command]
    done: asyncio.Future[list[Any]]


@dataclass
class _Return:
    scope: int
    ret: pb.Return


class _Conn:
    """The socket, and the order things may go out in."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.reader = reader
        self.writer = writer
        # The calls being answered, innermost last.
        self.frames: list[int] = []
        self.next_frame = 0
        # Requests sent and not yet answered, each with how deep in calls it was sent.
        self.inflight: list[tuple[int, asyncio.Future[list[Any]]]] = []
        self.queue: deque[_Request | _Return] = deque()
        self.handlers: dict[int, Handler] = {}
        self.next_id = 1
        self.done = asyncio.Event()
        self.tasks: set[asyncio.Task[None]] = set()  # callbacks running
        self.client: Any = None  # replies are encoded with it (callbacks in them)
        self.reading = asyncio.create_task(self._read())

    # ---- what may go out ----

    def active(self) -> int:
        """Who may send now: the innermost call, or the top level."""
        return self.frames[-1] if self.frames else 0

    def scope_of(self, scope: int) -> int:
        """A request from a call that has already returned is a top-level one."""
        return scope if scope in self.frames else 0

    def busy(self) -> bool:
        """One request at a time per level: duckwm answers before anything else goes."""
        return bool(self.inflight) and self.inflight[-1][0] == len(self.frames)

    def submit(self, op: _Request | _Return) -> None:
        if self.done.is_set():
            if isinstance(op, _Request) and not op.done.done():
                op.done.set_exception(Closed("duckwm closed the connection"))
            return
        self.queue.append(op)
        self._flush()

    def _flush(self) -> None:
        while not self.busy():
            active = self.active()
            op = next((o for o in self.queue if self.scope_of(o.scope) == active), None)
            if op is None:
                return
            self.queue.remove(op)
            if isinstance(op, _Request):
                self._write(pb.ClientMessage(request=pb.Request(commands=op.commands)))
                self.inflight.append((len(self.frames), op.done))
            else:
                self._write(pb.ClientMessage(ret=op.ret))
                self.frames.pop()

    def _write(self, msg: pb.ClientMessage) -> None:
        body = msg.SerializeToString()
        self.writer.write(struct.pack("<I", len(body)) + body)

    # ---- what comes in ----

    async def _read(self) -> None:
        try:
            while True:
                (n,) = struct.unpack("<I", await self.reader.readexactly(4))
                if n > MAX_FRAME:
                    break
                msg = pb.ServerMessage()
                msg.ParseFromString(await self.reader.readexactly(n))
                self._incoming(msg)
                self._flush()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._fail_all()

    def _incoming(self, msg: pb.ServerMessage) -> None:
        match msg.WhichOneof("kind"):
            case "response":
                if not self.inflight:
                    return
                _, done = self.inflight.pop()
                if done.done():
                    return
                r = msg.response
                if r.error:
                    done.set_exception(Rejected(r.error))
                else:
                    done.set_result([from_value(v) for v in r.results])
            case "call":
                call = msg.call
                # Not set off by our request: a request still in flight crossed
                # this call on the wire. duckwm runs it inside the call and
                # answers it first, so it belongs to the new frame now, and
                # keeps that level busy until its response arrives.
                if not call.nested:
                    depth = len(self.frames)
                    self.inflight = [
                        (d + 1 if d == depth else d, f) for d, f in self.inflight
                    ]
                self.next_frame += 1
                frame = self.next_frame
                self.frames.append(frame)
                self._call(frame, call)

    def _call(self, frame: int, call: pb.Call) -> None:
        """Run the callback in a task of its own, in its frame's scope."""
        handler = self.handlers.get(call.callback)

        async def run() -> None:
            ret = pb.Return()
            if handler is None:
                ret.err = "no such callback"
            else:
                try:
                    _fill_return(ret, await handler(call.args), self.client)
                except (
                    Rejected
                ) as e:  # a nested request was refused: say what duckwm said
                    ret.err = str(e)
                except Closed:
                    return
                except Exception as e:  # noqa: BLE001 - any callback error goes back as `err`
                    traceback.print_exc(file=sys.stderr)
                    ret.err = f"{type(e).__name__}: {e}"
            self.submit(_Return(frame, ret))

        ctx = contextvars.copy_context()
        ctx.run(_scope.set, frame)
        task = asyncio.get_running_loop().create_task(run(), context=ctx)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _fail_all(self) -> None:
        """The connection is gone: everyone waiting hears so."""
        self.done.set()
        for _, f in self.inflight:
            if not f.done():
                f.set_exception(Closed("duckwm closed the connection"))
        for op in self.queue:
            if isinstance(op, _Request) and not op.done.done():
                op.done.set_exception(Closed("duckwm closed the connection"))
        self.inflight.clear()
        self.queue.clear()
        self.writer.close()


def _fill_return(ret: pb.Return, r: Reply, client: Any) -> None:
    ret.commands.extend(encode_all(client, r.commands))
    if r.made is not None:
        ret.callback = r.made
    if r.layout is not None:
        ret.layout.CopyFrom(r.layout)
    if r.state is not UNSET:
        ret.state.CopyFrom(to_value(r.state))


class Client(AsyncApi):
    """A connection to duckwm on asyncio. Every command is an async method
    (await c.focus(3)); cmd.focus(3) is the same command as a value, for binds,
    batches and callback replies.

    Callbacks may be plain functions or coroutines. Inside one, requests run
    in the transaction duckwm is waiting on; requests from elsewhere wait.
    """

    def __init__(self, conn: _Conn, peer_pid: int) -> None:
        self._conn = conn
        self._pid = peer_pid
        self._factories: dict[Arranger, pb.Callback] = {}

    @classmethod
    async def connect(
        cls, path: str | None = None, *, sock: socket.socket | None = None
    ) -> "Client":
        """Connect at path (default: socket_path()), or use an already connected sock."""
        if sock is None:
            reader, writer = await asyncio.open_unix_connection(path or socket_path())
        else:
            reader, writer = await asyncio.open_unix_connection(sock=sock)
        raw = writer.get_extra_info("socket")
        creds = raw.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        pid, _uid, _gid = struct.unpack("3i", creds)
        conn = _Conn(reader, writer)
        c = cls(conn, pid)
        conn.client = c
        return c

    def close(self) -> None:
        self._conn.writer.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()
        await self.closed()

    async def closed(self) -> None:
        """Returns once the connection is gone: serving binds and arrangers
        until duckwm goes away is `await c.closed()`."""
        await self._conn.done.wait()

    # ---- requests ----

    async def run(self, *cmds: CommandLike) -> list[Any]:
        """Run the commands as one transaction; one result per command.
        Raises Rejected if duckwm refused them, in which case nothing changed."""
        commands = [c for x in cmds for c in encode_all(self, x)]
        done: asyncio.Future[list[Any]] = asyncio.get_running_loop().create_future()
        self._conn.submit(_Request(_scope.get(), commands, done))
        return await done

    # ---- which duckwm ----

    @functools.cached_property
    def platform(self) -> Literal["x11", "river"]:
        """Which duckwm this is, from its executable's name (duckwm-river on Wayland)."""
        return platform_of(self._pid)

    @functools.cached_property
    def startup(self) -> bool:
        """True for the first connection to this duckwm process, from any client
        library: for things to do once per session, like autostart."""
        return first_connection(self._pid)

    # ---- callbacks ----

    def register(self, handler: Handler) -> pb.Callback:
        """A raw callback: an async handler(args) returning a Reply."""
        cid = self._conn.next_id
        self._conn.next_id += 1
        self._conn.handlers[cid] = handler
        return pb.Callback(id=cid)

    def callback(self, v: CallbackLike) -> pb.Callback:
        """A callback for duckwm to call: a function or coroutine function (key:
        fn(), rule: fn(event, id), read_line: fn(text), arranger: fn(snapshot)),
        an Arranger (its factory), or an existing id."""
        if isinstance(v, pb.Callback):
            return v
        if isinstance(v, int):
            return pb.Callback(id=v)
        if isinstance(v, Arranger):
            return self._factory(v)
        if callable(v):
            return self.register(_adapt(v))
        raise TypeError(f"expected a callback, got {type(v).__name__}")

    async def bind(
        self, combo: str, action: "CallbackLike | CommandLike | list[CommandLike]"
    ) -> None:
        """Bind a key to: an arranger (switches the workspace to it), a function
        or coroutine (called here), or commands (run by duckwm itself, so the key
        doesn't wait on this process)."""
        k = KeyCombo.parse(combo)
        if isinstance(action, Arranger):
            action = cmd.set_arranger(action)
        if callable(action):
            await self.run(
                Cmd("bind", {"mods": k.mods, "key": k.key, "callback": action})
            )
        else:
            cmds = action if isinstance(action, (list, tuple)) else [action]
            await self.run(
                Cmd("bind_commands", {"mods": k.mods, "key": k.key, "commands": cmds})
            )

    async def default_arranger(self, layout: Arranger) -> None:
        """New workspaces, and those whose arranger went away, get layout; the current one now."""
        await self.run(cmd.set_default_arranger(layout), cmd.set_arranger(layout))

    def _factory(self, layout: Arranger) -> pb.Callback:
        """One arranger callback and one factory per layout: state lives in duckwm,
        so every workspace shares the same function."""
        if layout in self._factories:
            return self._factories[layout]
        arr = self.register(_adapt(layout))

        async def made(_args: pb.CallArgs) -> Reply:
            return Reply(made=arr.id)

        fac = self.register(made)
        self._factories[layout] = fac
        return fac

    # ---- typed queries ----

    async def get_keybinds(self) -> list[KeyCombo]:
        """Every bound key combination, once each."""
        return [
            KeyCombo(b["mods"], b["key"])
            for b in (await self.run(cmd.get_keybinds()))[0]
        ]

    async def get_workspace_states(self) -> list[Workspace]:
        """The workspaces at the current level, in order."""
        return [
            Workspace(w["position"], w["state"], w.get("id"))
            for w in (await self.run(cmd.get_workspace_states()))[0]
        ]

    # ---- names ----
    @contextlib.asynccontextmanager
    async def within(self, path: str) -> AsyncIterator[None]:
        """Binds, rules and settings made inside apply only in path
        ("DP-1/web/chat"): async with c.within("web"): await c.bind(...).
        Nested, the paths add up. The scope is the connection's, not the
        task's: requests other tasks make meanwhile go into it too."""
        outer = await self.get_scope()
        await self.set_scope(scope_path(outer, path))
        try:
            yield
        finally:
            await self.set_scope(outer)


def _adapt(fn: Callable[..., Any]) -> Handler:
    """A plain function or coroutine function as a handler: its arguments
    depend on what's calling."""

    async def handler(args: pb.CallArgs) -> Reply:
        match args.WhichOneof("kind"):
            case "rule":
                r = fn(args.rule.event, args.rule.id)
            case "arranger":
                r = fn(Snapshot(args.arranger))
            case "line":
                r = fn(args.line.text)
            case _:
                r = fn()
        if inspect.isawaitable(r):
            r = await r
        return _as_reply(r)

    return handler


# ---- for good ----

log = logging.getLogger("duckwm")


async def forever(setup: Callable[[Client], Any], path: str | None = None) -> None:
    """Run setup(c) (a function or coroutine function) on every connection,
    then serve until it closes; when duckwm goes away, wait for it and do it
    all again. A config that fails is retried with a growing delay."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    delay = 1.0
    waiting = False
    while True:
        try:
            c = await Client.connect(path)
        except (FileNotFoundError, ConnectionRefusedError):
            if not waiting:
                log.info("waiting for duckwm")
                waiting = True
            await asyncio.sleep(1)
            continue
        waiting = False
        log.info("connected")
        async with c:
            try:
                r = setup(c)
                if inspect.isawaitable(r):
                    await r
                delay = 1.0
                await c.closed()
                log.info("disconnected")
                continue
            except Closed:
                log.info("disconnected")
                continue
            except Rejected as e:
                log.error("duckwm rejected the config: %s", e)
            except Exception:
                log.exception("config failed")
        # Broken config: back off until it's fixed (or duckwm restarts).
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


__all__ = ["Client", "Closed", "Rejected", "forever"]
