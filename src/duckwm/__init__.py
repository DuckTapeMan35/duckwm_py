"""Client library for duckwm."""

from . import cmd
from ._encode import Cmd
from ._forever import forever
from ._mods import KeyCombo
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
    grid_cell_abs,
    key,
    node,
    ref,
    reply,
    split,
    track,
)
from .client import Client, Rejected, Workspace
from .geometry import Rect
from .tree import chunks, col, row

__version__ = "0.1.0"
__all__ = [
    "Arranger",
    "Client",
    "Closed",
    "Cmd",
    "KeyCombo",
    "Rect",
    "Rejected",
    "Reply",
    "Snapshot",
    "Workspace",
    "arranger",
    "at",
    "chunks",
    "cmd",
    "col",
    "fill",
    "fixed",
    "forever",
    "grid_cell",
    "grid_cell_abs",
    "key",
    "node",
    "ref",
    "reply",
    "row",
    "split",
    "track",
]
