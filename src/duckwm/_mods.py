MODS = {
    "shift": 1,
    "lock": 2,
    "ctrl": 4,
    "control": 4,
    "alt": 8,
    "mod2": 16,
    "mod3": 32,
    "super": 64,
    "mod5": 128,
}


def parse_mods(spec: str) -> int:
    """ "super+shift" -> 65."""
    bits = 0
    for m in filter(None, spec.split("+")):
        try:
            bits |= MODS[m.lower()]
        except KeyError:
            raise ValueError(f"unknown modifier {m!r} in {spec!r}") from None
    return bits
