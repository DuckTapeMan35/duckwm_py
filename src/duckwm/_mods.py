from dataclasses import dataclass

# X's modifier bits, which duckwm uses on both backends, in the order combos
# are written: "super+ctrl+alt+shift+q".
ORDER = [
    ("super", 64),
    ("ctrl", 4),
    ("alt", 8),
    ("shift", 1),
    ("lock", 2),
    ("mod2", 16),
    ("mod3", 32),
    ("mod5", 128),
]
MODS = dict(ORDER) | {"control": 4, "mod4": 64, "mod1": 8}


def parse_mods(spec: str) -> int:
    """ "super+shift" -> 65."""
    bits = 0
    for m in filter(None, spec.split("+")):
        try:
            bits |= MODS[m.lower()]
        except KeyError:
            raise ValueError(f"unknown modifier {m!r} in {spec!r}") from None
    return bits


def mod_names(mods: int) -> list[str]:
    """65 -> ["super", "shift"], in ORDER."""
    return [name for name, bit in ORDER if mods & bit]


@dataclass(frozen=True)
class KeyCombo:
    """A bound key: modifier bits plus a keysym name ("q", "Return")."""

    mods: int
    key: str

    @classmethod
    def parse(cls, combo: str) -> "KeyCombo":
        """ "super+shift+a": the key is the last part, the rest are modifiers."""
        mods, _, key = combo.rpartition("+")
        if not key:
            raise ValueError(f"missing key in {combo!r}")
        return cls(parse_mods(mods), key)

    def __str__(self) -> str:
        """ "super+shift+q"."""
        return "+".join([*mod_names(self.mods), self.key])
