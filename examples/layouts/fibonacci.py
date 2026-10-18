"""A golden spiral, as nested splits: resizable, and resizes stick."""

from duckwm import Snapshot, arranger, reply, track
from duckwm.tree import col, row


def spiral(ws, i=0):
    if len(ws) == 1:
        return ws[0]
    rest = spiral(ws[1:], i + 1)
    split = row if i % 2 == 0 else col
    if i % 4 < 2:  # window on the left / top
        return split(ws[0], rest, ratios=[0.618, 0.382])
    return split(rest, ws[0], ratios=[0.382, 0.618])  # on the right / bottom


@arranger
def fibonacci(s: Snapshot):
    order = track(s, s.state or [])
    return reply(s, spiral(order) if order else None, state=order)
