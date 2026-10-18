"""Layouts as nested rows and columns.

    reply(s, row(a, col(b, c), ratios=[0.6, 0.4]))

compiles to keyed containers holding splits. A container's ratios as duckwm
has them (after resizes) win over the ones given here, as long as it still
has the same number of children, so resizes stick until the shape changes.

Containers are keyed depth-first from 0 unless given key=: fine while the shape
only grows at the end (fair, tile, a spiral); layouts that restructure (dwindle)
should give their containers keys of their own, and keep them in state.
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from ._proto import duckwm_pb2 as pb
from .arrange import Snapshot, fixed, key, node, split

Axis = Literal["h", "v"]
Tree = "int | Branch"  # a window id, or a branch
T = TypeVar("T")


@dataclass
class Branch:
    axis: Axis
    children: list["int | Branch"]
    ratios: Sequence[float] | None = None
    key: int | None = None
    _k: int = field(default=-1, repr=False)  # assigned at compile time


def _branch(
    axis: Axis,
    children: Iterable["int | Branch | None"],
    ratios: Sequence[float] | None,
    k: int | None,
):
    kids = [c for c in children if c is not None]
    if not kids:
        return None
    if len(kids) == 1 and k is None:
        return kids[0]  # nothing to split: the child takes its parent's place
    if ratios is not None and len(ratios) != len(kids):
        ratios = None
    return Branch(axis, kids, ratios, k)


def row(
    *children: "int | Branch | None",
    ratios: Sequence[float] | None = None,
    key: int | None = None,
):
    """Children side by side. None children are skipped; one child is just that child."""
    return _branch("h", children, ratios, key)


def col(
    *children: "int | Branch | None",
    ratios: Sequence[float] | None = None,
    key: int | None = None,
):
    """Children stacked top to bottom."""
    return _branch("v", children, ratios, key)


def chunks(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """items in consecutive pieces of size (the last may be shorter)."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _branches(t: "int | Branch") -> Iterator[Branch]:
    if isinstance(t, Branch):
        yield t
        for c in t.children:
            yield from _branches(c)


def compile_tree(s: Snapshot, t: "int | Branch | None") -> list[pb.NodeSpec]:
    if t is None:
        return []
    w, h = s.size
    if not isinstance(t, Branch):
        return [node(t, *fixed(0, 0, w, h))]

    # Keys: explicit ones as given, the rest depth-first from 0, skipping those.
    branches = list(_branches(t))
    taken = {b.key for b in branches if b.key is not None}
    auto = (k for k in range(len(branches) + len(taken)) if k not in taken)
    for b in branches:
        b._k = b.key if b.key is not None else next(auto)

    nodes: list[pb.NodeSpec] = []
    for b in branches:
        stored = s.ratios_of(b._k)
        ratios = (
            stored
            if stored and len(stored) == len(b.children) and all(r > 0 for r in stored)
            else b.ratios
        )
        kids = [key(c._k) if isinstance(c, Branch) else c for c in b.children]
        cons = fixed(0, 0, w, h) if b is t else []  # the root fills the work area
        cons.append(split(key(b._k), b.axis, kids, ratios))
        nodes.append(node(key(b._k), *cons))
        nodes.extend(node(c) for c in b.children if not isinstance(c, Branch))
    return nodes
