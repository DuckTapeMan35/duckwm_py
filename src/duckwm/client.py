"""The client: requests, callbacks, and answering duckwm's calls."""

import contextlib
import functools
import os
import select
import socket
import sys
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from . import cmd
from ._api import Api
from ._encode import CallbackLike, Cmd, CommandLike, encode_all
from ._mods import KeyCombo
from ._proto import duckwm_pb2 as pb
from ._reply import UNSET, Reply
from ._wire import Closed, Connection
from .arrange import Arranger, Snapshot
from .value import from_value, to_value


class Rejected(Exception):
    """duckwm refused a batch; nothing in it took effect."""


def parse_combo(combo: str) -> tuple[int, str]:
    """ "super+shift+a" -> (65, "a"). The key is the last part, as an xkb keysym name."""
    k = KeyCombo.parse(combo)
    return k.mods, k.key


@dataclass(frozen=True)
class Workspace:
    """One workspace at the current level, from get_workspace_states."""

    position: int  # 1-based
    state: Literal["focused", "active", "inactive"]
    id: int | None = (
        None  # its node id, for set_name; None from a duckwm too old to say
    )


def scope_path(outer: str | None, path: str) -> str:
    """within's path inside another: they add up."""
    return f"{outer}/{path}" if outer else path


Handler = Callable[[pb.CallArgs], Reply | None]


def platform_of(pid: int) -> Literal["x11", "river"]:
    exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
    return "river" if "river" in exe else "x11"


def first_connection(pid: int) -> bool:
    """Whether this is the first to ask for duckwm process pid: a marker file
    every client library shares."""
    run = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    try:
        (run / f"duckwm-started-{pid}").touch(exist_ok=False)
        return True
    except FileExistsError:
        return False


def notice_args(n: pb.Notice) -> tuple[str, int, int | None]:
    """A subscription's arguments: topic ("scene", "focus", "names", "outputs"),
    the scene serial, and the node it's about (or None)."""
    topic = pb.Topic.Name(n.topic).removeprefix("TOPIC_").lower()
    return topic, n.serial, n.id if n.HasField("id") else None


def _as_reply(result: Any) -> Reply:
    """A callback may return a Reply, a command or a list of them, or nothing."""
    if isinstance(result, Reply):
        return result
    if result is None:
        return Reply()
    return Reply(
        commands=list(result) if isinstance(result, (list, tuple)) else [result]
    )


class Client(Api):
    """A connection to duckwm. Every command is a method (c.focus(3)), run now;
    cmd.focus(3) is the same command as a value, for binds and batches."""

    def __init__(
        self, path: str | None = None, *, sock: socket.socket | None = None
    ) -> None:
        """Connect at path (default: socket_path()), or use an already connected sock."""
        self._conn = Connection(path, sock=sock)
        self._handlers: dict[int, Handler] = {}
        self._factories: dict[Arranger, pb.Callback] = {}
        self._next_id = 1

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---- requests ----
    def run(self, *cmds: CommandLike) -> list[Any]:
        """Run the commands as one transaction; one result per command.

        Calls arriving meanwhile (from what the commands set off) are answered.
        Raises Rejected if duckwm refused them, in which case nothing changed.
        """
        req = pb.Request(commands=[c for x in cmds for c in encode_all(self, x)])
        self._conn.send(pb.ClientMessage(request=req))
        # A call not set off by this request (not nested) crossed it on the
        # wire: duckwm runs the request inside that call and answers it first,
        # so the call is handled once the answer is in.
        crossed: list[pb.Call] = []
        # Events wait for the response too: what a subscriber does is
        # requests of its own, which can't go out in the middle of this one.
        events: list[pb.Event] = []
        while True:
            msg = self._conn.recv()
            kind = msg.WhichOneof("kind")
            if kind == "response":
                break
            if kind == "event":
                events.append(msg.event)
            elif msg.call.nested:
                self._handle_call(msg.call)
            else:
                crossed.append(msg.call)
        for call in crossed:
            self._handle_call(call)
        for event in events:
            self._handle_event(event)
        if msg.response.error:
            raise Rejected(msg.response.error)
        return [from_value(v) for v in msg.response.results]

    def serve(self) -> None:
        """Answer duckwm's calls and events until the connection closes (raises Closed)."""
        while True:
            self._handle(self._conn.recv())

    def fileno(self) -> int:
        """The socket, for an event loop of your own (select, selectors...)."""
        return self._conn.sock.fileno()

    def dispatch(self) -> None:
        """When fileno() is readable: answer the calls waiting, without
        blocking for more. Raises Closed once duckwm has gone."""
        while select.select([self._conn.sock], [], [], 0)[0]:
            self._handle(self._conn.recv())

    # ---- typed queries ----
    def get_keybinds(self) -> list[KeyCombo]:
        """Every bound key combination, once each."""
        return [KeyCombo(b["mods"], b["key"]) for b in self.run(cmd.get_keybinds())[0]]

    def get_workspace_states(self) -> list[Workspace]:
        """The workspaces at the current level, in order."""
        return [
            Workspace(w["position"], w["state"], w.get("id"))
            for w in self.run(cmd.get_workspace_states())[0]
        ]

    # ---- names ----
    @contextlib.contextmanager
    def within(self, path: str) -> Iterator[None]:
        """Binds, rules and settings made inside apply only in path
        ("DP-1/web/chat"): with c.within("web"): c.bind(...). Nested, the
        paths add up. The scope is this connection's, whatever makes the
        requests; it's put back on the way out, even after an error."""
        outer = self.get_scope()
        self.set_scope(scope_path(outer, path))
        try:
            yield
        finally:
            self.set_scope(outer)

    # ---- which duckwm ----
    @functools.cached_property
    def platform(self) -> Literal["x11", "river"]:
        return platform_of(self._conn.peer_pid)

    @functools.cached_property
    def startup(self) -> bool:
        return first_connection(self._conn.peer_pid)

    # ---- callbacks ----
    def register(self, handler: Handler) -> pb.Callback:
        """A raw callback: handler gets the CallArgs, returns a Reply or None."""
        cid = self._next_id
        self._next_id += 1
        self._handlers[cid] = handler
        return pb.Callback(id=cid)

    def callback(self, v: CallbackLike) -> pb.Callback:
        """A callback for duckwm to call: a function (key: fn(), rule: fn(event, id),
        read_line: fn(text), arranger: fn(snapshot), subscribe: fn(topic, serial, id)), an Arranger (its factory),
        or an existing id."""
        if isinstance(v, pb.Callback):
            return v
        if isinstance(v, int):
            return pb.Callback(id=v)
        if isinstance(v, Arranger):
            return self._factory(v)
        if callable(v):
            return self.register(self._adapt(v))
        raise TypeError(f"expected a callback, got {type(v).__name__}")

    def bind(
        self, combo: str, action: "CallbackLike | CommandLike | list[CommandLike]"
    ) -> None:
        """Bind a key to: an arranger (switches the workspace to it), a function
        (called here), or commands (run by duckwm itself, so the key doesn't wait
        on this process)."""
        mods, key = parse_combo(combo)
        if isinstance(action, Arranger):
            action = cmd.set_arranger(action)
        if callable(action):
            self.run(Cmd("bind", {"mods": mods, "key": key, "callback": action}))
        else:
            cmds = action if isinstance(action, (list, tuple)) else [action]
            self.run(Cmd("bind_commands", {"mods": mods, "key": key, "commands": cmds}))

    def default_arranger(self, layout: Arranger) -> None:
        """New workspaces, and those whose arranger went away, get layout; the current one now."""
        self.run(cmd.set_default_arranger(layout), cmd.set_arranger(layout))

    def _factory(self, layout: Arranger) -> pb.Callback:
        """One arranger callback and one factory per layout: state lives in duckwm,
        so every workspace shares the same function."""
        if layout in self._factories:
            return self._factories[layout]
        arr = self.register(self._adapt(layout))
        fac = self.register(lambda _args: Reply(made=arr.id))
        self._factories[layout] = fac
        return fac

    def _adapt(self, fn: Callable[..., Any]) -> Handler:
        """A plain function as a handler: its arguments depend on what's calling."""

        def handler(args: pb.CallArgs) -> Reply:
            match args.WhichOneof("kind"):
                case "rule":
                    return _as_reply(fn(args.rule.event, args.rule.id))
                case "arranger":
                    return _as_reply(fn(Snapshot(args.arranger)))
                case "line":
                    return _as_reply(fn(args.line.text))
                case "notice":
                    return _as_reply(fn(*notice_args(args.notice)))
                case _:
                    return _as_reply(fn())

        return handler

    # ---- duckwm calling us ----
    def _handle_call(self, call: pb.Call) -> None:
        ret = pb.Return()
        handler = self._handlers.get(call.callback)
        if handler is None:
            ret.err = "no such callback"
        else:
            try:
                r = handler(call.args)
                if r is not None:
                    ret.commands.extend(encode_all(self, r.commands))
                    if r.made is not None:
                        ret.callback = r.made
                    if r.layout is not None:
                        ret.layout.CopyFrom(r.layout)
                    if r.state is not UNSET:
                        ret.state.CopyFrom(to_value(r.state))
            except Rejected as e:  # a nested request was refused: say what duckwm said
                ret.err = str(e)
            except Exception as e:  # noqa: BLE001 - any callback error goes back to duckwm as `err`
                traceback.print_exc(file=sys.stderr)
                ret.err = f"{type(e).__name__}: {e}"
        self._conn.send(pb.ClientMessage(ret=ret))

    def _handle(self, msg: pb.ServerMessage) -> None:
        match msg.WhichOneof("kind"):
            case "call":
                self._handle_call(msg.call)
            case "event":
                self._handle_event(msg.event)

    def _handle_event(self, event: pb.Event) -> None:
        """A subscriber's handler. Nothing goes back: duckwm isn't waiting. The
        commands it returns run as a request of their own; an error is printed,
        there being no one to give it to."""
        handler = self._handlers.get(event.callback)
        if handler is None:
            return
        try:
            r = handler(pb.CallArgs(notice=event.notice))
            if r is not None and r.commands:
                self.run(*r.commands)
        except Closed:
            raise
        except Exception:  # noqa: BLE001 - nowhere to send it
            traceback.print_exc(file=sys.stderr)


__all__ = ["Client", "Closed", "Rejected", "parse_combo"]
