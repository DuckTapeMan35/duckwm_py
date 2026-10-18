"""Commands as values (Cmd), and turning them into protobuf.

Encoding needs the client: a function or arranger in a Callback field becomes a
callback registered with it. Every command gets the same conversions:

  enums     "left" for DIR_LEFT (or the full name, or the number)
  Ref       a window id, or key(k)
  Callback  a function, an Arranger, or a callback id
  Value     plain Python (None, bool, int, float, str, list, dict)
  colors    "#rrggbb" in color fields
  messages  a dict of fields, or the protobuf message itself
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from google.protobuf import json_format
from google.protobuf.descriptor import FieldDescriptor as FD
from google.protobuf.message import Message

from ._proto import duckwm_pb2 as pb
from .arrange import Arranger, RefLike, ref
from .value import to_value

if TYPE_CHECKING:
    from .client import Client


@dataclass(frozen=True)
class Cmd:
    """A command by name and fields, encoded when it's sent."""

    name: str
    args: Mapping[str, Any] = field(default_factory=dict)


CommandLike = Cmd | pb.Command | Mapping[str, Any]
CallbackLike = int | pb.Callback | Arranger | Callable[..., Any]

__all__ = ["CallbackLike", "Cmd", "CommandLike", "RefLike", "encode", "encode_all"]


def encode(c: Client, x: CommandLike) -> pb.Command:
    if isinstance(x, pb.Command):
        return x
    if isinstance(x, Cmd):
        out = pb.Command()
        sub = getattr(out, x.name)
        _fill(c, sub, x.args)
        sub.SetInParent()  # commands without fields
        return out
    if isinstance(x, Mapping):  # the Nix commands file's shape
        return json_format.ParseDict(x, pb.Command())
    raise TypeError(f"expected a command, got {type(x).__name__}")


def encode_all(c: Client, x: Any) -> list[pb.Command]:
    """One command, several, or none."""
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return [encode(c, i) for i in x]
    return [encode(c, x)]


def _repeated(f: FD) -> bool:
    r = getattr(f, "is_repeated", None)
    return r if isinstance(r, bool) else f.label == FD.LABEL_REPEATED


def _fill(c: Client, msg: Message, args: Mapping[str, Any]) -> None:
    fields = msg.DESCRIPTOR.fields_by_name
    for name, v in args.items():
        if v is None:
            continue
        f = fields[name]
        if _repeated(f):
            target = getattr(msg, name)
            for item in v:
                if f.type == FD.TYPE_MESSAGE:
                    _message(c, f, target.add(), item)
                else:
                    target.append(_scalar(f, item))
        elif f.type == FD.TYPE_MESSAGE:
            _message(c, f, getattr(msg, name), v)
        else:
            setattr(msg, name, _scalar(f, v))
    # set_arranger(fair) and friends: name it after the arranger unless told otherwise.
    if (
        isinstance(args.get("factory"), Arranger)
        and "name" in fields
        and args.get("name") is None
    ):
        msg.name = args["factory"].name  # type: ignore[attr-defined]


def _message(c: Client, f: FD, target: Message, v: Any) -> None:
    if isinstance(v, Message):
        target.CopyFrom(v)
        return
    match f.message_type.name:
        case "Ref":
            target.CopyFrom(ref(v))
        case "Callback":
            target.CopyFrom(c.callback(v))
        case "Value":
            target.CopyFrom(to_value(v))
        case "Command":
            target.CopyFrom(encode(c, v))
        case "LayoutSpec" if isinstance(v, (list, tuple)):
            target.nodes.extend(v)  # type: ignore[attr-defined]
        case _ if isinstance(v, Mapping):
            _fill(c, target, v)
            target.SetInParent()
        case other:
            raise TypeError(f"{f.name}: can't make a {other} from a {type(v).__name__}")


def _screaming(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()


def _scalar(f: FD, v: Any) -> Any:
    if f.type == FD.TYPE_ENUM and isinstance(v, str):
        e = f.enum_type
        prefix = _screaming(e.name) + "_"
        full = v if v.startswith(prefix) else prefix + v.upper()
        if full not in e.values_by_name:
            names = ", ".join(n.removeprefix(prefix).lower() for n in e.values_by_name)
            raise ValueError(f"{f.name}: {v!r} isn't one of {names}")
        return e.values_by_name[full].number
    if (
        isinstance(v, str)
        and v.startswith("#")
        and f.type not in (FD.TYPE_STRING, FD.TYPE_BYTES)
    ):
        return int(v[1:], 16)
    return v
