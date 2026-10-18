"""Columns of near-equal windows: two side by side, then as square as possible."""

from math import ceil, sqrt

from ..arrange import Snapshot, arranger, reply
from ..tree import chunks, col, row


@arranger
def fair(s: Snapshot):
    n = len(s.windows)
    rows = 1 if n == 2 else ceil(sqrt(n)) if n else 1
    return reply(s, row(*(col(*c) for c in chunks(s.windows, rows))))
