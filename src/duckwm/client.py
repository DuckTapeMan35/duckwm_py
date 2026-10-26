"""The client: requests, callbacks, and answering duckwm's calls."""

import functools
import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Self

from . import cmd
from ._api import Api
from ._encode import CallbackLike, Cmd, CommandLike, encode_all
from ._mods import parse_mods
from ._proto import duckwm_pb2 as pb
from ._reply import UNSET, Reply
from ._wire import Closed, Connection
from .arrange import Arranger, Snapshot
from .value import from_value, to_value


class Rejected(Exception):
    """duckwm refused a batch; nothing in it took effect."""


def parse_combo(combo: str) -> tuple[int, str]:
    """ "super+shift+a" -> (65, "a"). The key is the last part, as an xkb keysym name."""
    mods, _, key = combo.rpartition("+")
    if not key:
        raise ValueError(f"missing key in {combo!r}")
    return parse_mods(mods), key


Handler = Callable[[pb.CallArgs], Reply | None]


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

    def __init__(self, path: str | None = None) -> None:
        self._conn = Connection(path)
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
        while True:
            msg = self._conn.recv()
            if msg.WhichOneof("kind") == "response":
                break
            if msg.call.nested:
                self._handle_call(msg.call)
            else:
                crossed.append(msg.call)
        for call in crossed:
            self._handle_call(call)
        if msg.response.error:
            raise Rejected(msg.response.error)
        return [from_value(v) for v in msg.response.results]

    def serve(self) -> None:
        """Answer duckwm's calls until the connection closes (raises Closed)."""
        while True:
            msg = self._conn.recv()
            if msg.WhichOneof("kind") == "call":
                self._handle_call(msg.call)

    # ---- which duckwm ----

    @functools.cached_property
    def platform(self) -> Literal["x11", "river"]:
        """Which duckwm this is, from its executable's name (duckwm-river on Wayland)."""
        exe = os.path.basename(os.readlink(f"/proc/{self._conn.peer_pid}/exe"))
        return "river" if "river" in exe else "x11"

    @functools.cached_property
    def startup(self) -> bool:
        """True for the first connection to this duckwm process, from any client
        using this library: for things to do once per session, like autostart."""
        run = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        try:
            (run / f"duckwm-started-{self._conn.peer_pid}").touch(exist_ok=False)
            return True
        except FileExistsError:
            return False

    # ---- callbacks ----

    def register(self, handler: Handler) -> pb.Callback:
        """A raw callback: handler gets the CallArgs, returns a Reply or None."""
        cid = self._next_id
        self._next_id += 1
        self._handlers[cid] = handler
        return pb.Callback(id=cid)

    def callback(self, v: CallbackLike) -> pb.Callback:
        """A callback for duckwm to call: a function (key: fn(), rule: fn(event, id),
        arranger: fn(snapshot)), read_line: fn(text), an Arranger (its factory), or an existing id."""
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


__all__ = ["Client", "Closed", "Rejected", "parse_combo"]
