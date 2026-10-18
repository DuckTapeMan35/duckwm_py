"""Master and stack: the first window on the left, the rest stacked on the right."""

from ..arrange import Snapshot, arranger, reply, track
from ..tree import col, row


@arranger
def tile(s: Snapshot):
    order = track(s, s.state or [])
    master, *stack = order or [None]
    return reply(s, row(master, col(*stack), ratios=[0.55, 0.45]), state=order)
