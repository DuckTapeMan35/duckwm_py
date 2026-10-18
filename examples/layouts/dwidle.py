"""Dwindle: each new window splits the focused one, alternating direction, on
the side of the cursor. Splits keep keys of their own, stored in state, so a
resized split keeps its ratios while the tree changes around it.

A tree is a window id, or [key, axis, first, second].
"""

from duckwm import Snapshot, arranger, reply
from duckwm.tree import col, row


def leaves(t):
    if t is None:
        return []
    if isinstance(t, int):
        return [t]
    return leaves(t[2]) + leaves(t[3])


def remove(t, w):
    """t without window w: its split collapses into the sibling."""
    if isinstance(t, int):
        return None if t == w else t
    k, axis, a, b = t
    a, b = remove(a, w), remove(b, w)
    if a is None:
        return b
    if b is None:
        return a
    return [k, axis, a, b]


def replace(t, target, new):
    if isinstance(t, int):
        return new if t == target else t
    k, axis, a, b = t
    return [k, axis, replace(a, target, new), replace(b, target, new)]


def area(s, w):
    g = s.geometry(w)
    return g.width * g.height if g else 0


def insert(s, st, w):
    """Split the focused window (or the largest, if it's tiny or gone) for w."""
    t = st["tree"]
    if t is None:
        return {**st, "tree": w}
    ls = leaves(t)
    target = max(ls, key=lambda x: area(s, x))
    if s.prev_id in ls:
        g = s.geometry(s.prev_id)
        if g is None or (g.width >= 20 and g.height >= 20):
            target = s.prev_id
    dx, dy = s.cursor  # cursor relative to the focused window's centre
    first = (dx if st["h"] else dy) < 0
    pair = [w, target] if first else [target, w]
    split = [st["key"], "h" if st["h"] else "v", *pair]
    return {"tree": replace(t, target, split), "h": not st["h"], "key": st["key"] + 1}


def build(t):
    if isinstance(t, int):
        return t
    k, axis, a, b = t
    return (row if axis == "h" else col)(build(a), build(b), key=k)


@arranger
def dwindle(s: Snapshot):
    st = s.state or {"tree": None, "h": True, "key": 1}
    present = set(s.windows)
    for w in leaves(st["tree"]):
        if w not in present:
            st["tree"] = remove(st["tree"], w)
    have = set(leaves(st["tree"]))
    for w in s.windows:
        if w not in have:
            st = insert(s, st, w)
    return reply(s, build(st["tree"]) if st["tree"] is not None else None, state=st)
