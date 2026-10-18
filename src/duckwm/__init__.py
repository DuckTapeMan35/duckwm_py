"""Client library for duckwm."""

from . import cmd
from ._encode import Cmd
from ._forever import forever
from ._reply import Reply
from ._wire import Closed
from .arrange import (
    Arranger,
    Snapshot,
    arranger,
    at,
    fill,
    fixed,
    grid_cell,
    key,
    node,
    ref,
    reply,
    split,
    track,
)
from .client import Client, Rejected
from .geometry import Rect
from .tree import chunks, col, row

__version__ = "0.1.0"
__all__ = [
    "Arranger",
    "Client",
    "Closed",
    "Rejected",
    "Reply",
    "Snapshot",
    "Rect",
    "Cmd",
    "chunks",
    "col",
    "row",
    "arranger",
    "at",
    "cmd",
    "fill",
    "fixed",
    "grid_cell",
    "key",
    "node",
    "forever",
    "ref",
    "reply",
    "split",
    "track",
]
