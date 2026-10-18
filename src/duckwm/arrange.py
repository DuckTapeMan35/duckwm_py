"""Arrangers: a function from a workspace's Snapshot to its whole layout.

Window constraints are relative to the work area. Containers are named by keys
(key(0), key(1), ...): duckwm creates the ones a layout mentions and drops the
ones it stops mentioning.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, Literal, overload

if TYPE_CHECKING:
    from .tree import Branch

from ._proto import duckwm_pb2 as pb
from ._reply import UNSET, Reply
from .geometry import Rect
from .value import from_value


def _rect(r: pb.Rect) -> Rect:
    return Rect(r.x, r.y, r.width, r.height)


class Snapshot:
    """What an arranger sees: the event, the workspace's tiled windows, and its
    layout and state as duckwm last stored them."""

    def __init__(self, raw: pb.Snapshot) -> None:
        self.raw = raw
        self.event: str = (
            pb.ArrangerEvent.Name(raw.event).removeprefix("ARRANGER_EVENT_").lower()
        )
        self.id: int = raw.id
        self.prev_id: int | None = raw.prev_id if raw.HasField("prev_id") else None
        self.focused: int | None = raw.focused if raw.HasField("focused") else None
        self.work_area = _rect(raw.work_area)
        self.cursor: tuple[int, int] = (raw.cursor_dx, raw.cursor_dy)
        self.state: Any = from_value(raw.state) if raw.HasField("state") else None
        self._geometry = {w.id: _rect(w.geometry) for w in raw.windows}
        gone = raw.id if self.event == "unmap" else None
        # The tiled windows, in the order they appeared; not the one being unmapped.
        self.windows: list[int] = [w.id for w in raw.windows if w.id != gone]

    @property
    def size(self) -> tuple[int, int]:
        return self.work_area.width, self.work_area.height

    @property
    def area(self) -> Rect:
        """The work area, relative to itself: what window constraints are measured in."""
        return Rect(0, 0, self.work_area.width, self.work_area.height)

    def geometry(self, id: int) -> Rect | None:
        return self._geometry.get(id)

    def ratios_of(self, k: int) -> list[float] | None:
        """The split ratios keyed container k has now: resizes end up here."""
        for n in self.raw.layout.nodes:
            if n.id.WhichOneof("kind") != "key" or n.id.key != k:
                continue
            for c in n.constraints:
                if c.WhichOneof("kind") == "split":
                    return list(c.split.ratios)
        return None


# ---- refs and nodes ----

RefLike = int | pb.Ref


def key(k: int) -> pb.Ref:
    """A container, by key."""
    return pb.Ref(key=k)


def ref(r: RefLike) -> pb.Ref:
    """A window id, or a key(k)."""
    return r if isinstance(r, pb.Ref) else pb.Ref(id=r)


def node(r: RefLike, *constraints: pb.ConstraintSpec) -> pb.NodeSpec:
    return pb.NodeSpec(id=ref(r), constraints=constraints)


# ---- constraints ----


def fixed(x: int, y: int, w: int, h: int) -> list[pb.ConstraintSpec]:
    """Position and size, relative to the work area."""
    return [
        pb.ConstraintSpec(fixed_x=pb.ConstraintSpecFixedX(value=x)),
        pb.ConstraintSpec(fixed_y=pb.ConstraintSpecFixedY(value=y)),
        pb.ConstraintSpec(fixed_width=pb.ConstraintSpecFixedWidth(value=max(0, w))),
        pb.ConstraintSpec(fixed_height=pb.ConstraintSpecFixedHeight(value=max(0, h))),
    ]


def at(r: RefLike, x: "int | Rect", y: int = 0, w: int = 0, h: int = 0) -> pb.NodeSpec:
    """A node placed at a rectangle of the work area: at(win, rect) or at(win, x, y, w, h)."""
    if isinstance(x, Rect):
        x, y, w, h = x
    return node(r, *fixed(x, y, w, h))


def grid_cell(
    container: RefLike, col: int = 0, row: int = 0, cols: int = 1, rows: int = 1
) -> pb.ConstraintSpec:
    return pb.ConstraintSpec(
        grid_cell=pb.ConstraintSpecGridCell(
            col=col, row=row, cols=cols, rows=rows, container=ref(container)
        )
    )


def fill(container: RefLike) -> pb.ConstraintSpec:
    """The whole of container."""
    return grid_cell(container)


def split(
    container: RefLike,
    axis: Literal["h", "v"],
    children: Sequence[RefLike],
    ratios: Sequence[float] | None = None,
) -> pb.ConstraintSpec:
    """Divide container among children, side by side (h) or stacked (v); equal shares by default."""
    if ratios is None:
        ratios = [1 / len(children)] * len(children)
    return pb.ConstraintSpec(
        split=pb.ConstraintSpecSplit(
            container=ref(container),
            axis=pb.AXIS_H if axis == "h" else pb.AXIS_V,
            ratios=ratios,
            children=[ref(c) for c in children],
        )
    )


# ---- order and replies ----


def track(s: Snapshot, old: Iterable[Any]) -> list[int]:
    """Last time's window order, minus closed windows, plus new ones: a mapped
    window goes after the one that was focused, any others at the end."""
    present = set(s.windows)
    out = [i for i in old if i in present]
    for w in s.windows:
        if w in out:
            continue
        pos = len(out)
        if s.event == "map" and w == s.id and s.prev_id in out:
            pos = out.index(s.prev_id) + 1
        out.insert(pos, w)
    return out


def reply(
    s: Snapshot,
    layout: "Iterable[pb.NodeSpec] | int | Branch | None",
    state: Any = UNSET,
) -> Reply:
    """The whole layout (a tree from row/col, or nodes), and the state to keep;
    a newly mapped window gets focus."""
    from .tree import Branch, compile_tree  # tree builds on this module

    if layout is None or isinstance(layout, (int, Branch)):
        nodes = compile_tree(s, layout)
    else:
        nodes = list(layout)
    cmds = [pb.Command(focus=pb.Focus(id=s.id))] if s.event == "map" else []
    return Reply(commands=cmds, layout=pb.LayoutSpec(nodes=nodes), state=state)


# ---- arrangers ----

Layout = Callable[[Snapshot], Reply]


class Arranger:
    """A layout function and its name. Bind it to a key, or make it the default."""

    __slots__ = ("fn", "name")

    def __init__(self, fn: Layout, name: str) -> None:
        self.fn = fn
        self.name = name

    def __call__(self, s: Snapshot) -> Reply:
        return self.fn(s)

    def __repr__(self) -> str:
        return f"<arranger {self.name}>"


@overload
def arranger(fn: Layout, /) -> Arranger: ...
@overload
def arranger(name: str | None = None, /) -> Callable[[Layout], Arranger]: ...
def arranger(
    arg: Layout | str | None = None, /
) -> Arranger | Callable[[Layout], Arranger]:
    """@arranger, or @arranger("name"): the name shows up in duckwm (get_arranger_name)."""
    if callable(arg):
        return Arranger(arg, arg.__name__)

    def wrap(fn: Layout) -> Arranger:
        return Arranger(fn, arg or fn.__name__)

    return wrap
