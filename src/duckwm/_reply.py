"""What a callback hands back; shared by the client and arrangers."""

from dataclasses import dataclass, field
from typing import Any

from ._proto import duckwm_pb2 as pb

UNSET: Any = object()


@dataclass
class Reply:
    """Commands run in duckwm's transaction, and for an arranger its layout and
    next state (UNSET: leave the state alone); a factory names what it made."""

    commands: list[Any] = field(default_factory=list)
    layout: pb.LayoutSpec | None = None
    state: Any = UNSET
    made: int | None = None
