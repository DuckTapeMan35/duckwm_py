"""Arrangers, without a connection: track, and tile's layout."""

from duckwm import Snapshot, track
from duckwm._proto import duckwm_pb2 as pb
from duckwm.layouts import tile


def snap(
    event: str, id: int, prev: int | None, windows: list[int], state=None
) -> Snapshot:
    raw = pb.Snapshot(
        event=pb.ArrangerEvent.Value("ARRANGER_EVENT_" + event.upper()),
        id=id,
        work_area=pb.Rect(x=0, y=0, width=1000, height=500),
        windows=[pb.WindowInfo(id=w) for w in windows],
    )
    if prev is not None:
        raw.prev_id = prev
    if state is not None:
        from duckwm.value import to_value

        raw.state.CopyFrom(to_value(state))
    return Snapshot(raw)


def test_track_puts_a_new_window_after_the_focused_one():
    assert track(snap("map", 9, 1, [1, 2, 9]), [1, 2]) == [1, 9, 2]


def test_track_places_a_new_window_at_pre_map():
    # duckwm calls pre_map, then map, with the new window in both.
    placed = track(snap("pre_map", 9, 1, [1, 2, 9]), [1, 2])
    assert placed == [1, 9, 2]
    assert track(snap("map", 9, 1, [1, 2, 9]), placed) == [1, 9, 2]


def test_track_drops_closed_windows():
    s = snap("unmap", 2, 2, [1, 2, 3])
    assert s.windows == [1, 3]
    assert track(s, [1, 2, 3]) == [1, 3]


def test_tile_keeps_order_and_focuses_a_mapped_window():
    r = tile(snap("map", 3, 1, [1, 2, 3], state=[1, 2]))
    assert r.state == [1, 3, 2]
    assert r.commands[0].focus.id == 3
    root = r.layout.nodes[0]
    split = [c.split for c in root.constraints if c.WhichOneof("kind") == "split"][0]
    assert list(split.ratios) == [0.55, 0.45]
