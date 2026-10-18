"""Rectangle arithmetic for layouts that place windows exactly.

Amounts: a float is a fraction of the length, an int is pixels.
"""

from collections.abc import Sequence
from typing import Literal, NamedTuple

Side = Literal["left", "right", "top", "bottom"]
Axis = Literal["h", "v"]


def _length(total: int, amount: float | int) -> int:
    n = round(total * amount) if isinstance(amount, float) else amount
    return max(0, min(total, n))


class Rect(NamedTuple):
    x: int
    y: int
    width: int
    height: int

    def cut(self, side: Side, amount: float | int) -> tuple["Rect", "Rect"]:
        """Take a piece off one side: (piece, what's left)."""
        x, y, w, h = self
        match side:
            case "left":
                p = _length(w, amount)
                return Rect(x, y, p, h), Rect(x + p, y, w - p, h)
            case "right":
                p = _length(w, amount)
                return Rect(x + w - p, y, p, h), Rect(x, y, w - p, h)
            case "top":
                p = _length(h, amount)
                return Rect(x, y, w, p), Rect(x, y + p, w, h - p)
            case "bottom":
                p = _length(h, amount)
                return Rect(x, y + h - p, w, p), Rect(x, y, w, h - p)

    def split(self, axis: Axis, ratios: Sequence[float] | int) -> list["Rect"]:
        """Side by side (h) or stacked (v), by ratios or into n equal parts.
        The pieces cover the rectangle exactly: rounding goes to the edges between them."""
        if isinstance(ratios, int):
            ratios = [1.0] * ratios
        total = sum(ratios)
        length = self.width if axis == "h" else self.height
        out, acc, prev = [], 0.0, 0
        for r in ratios:
            acc += r
            end = round(length * acc / total)
            if axis == "h":
                out.append(Rect(self.x + prev, self.y, end - prev, self.height))
            else:
                out.append(Rect(self.x, self.y + prev, self.width, end - prev))
            prev = end
        return out

    def grid(self, rows: int, cols: int) -> list["Rect"]:
        """rows x cols cells, row by row."""
        return [cell for r in self.split("v", rows) for cell in r.split("h", cols)]
