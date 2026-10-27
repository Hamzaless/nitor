"""Helpers for applying user presets.

A preset is a small mapping of VCP code to value.  Values are written in a
fixed order: colour mode first, brightness and contrast last, because many
monitors reset picture settings when the colour mode changes.
"""

from __future__ import annotations

__all__ = ["CAPTURE_CODES", "APPLY_ORDER", "normalized"]

#: Values captured when the user saves the current state as a preset.
CAPTURE_CODES: tuple[int, ...] = (0x10, 0x12, 0x14, 0x72, 0x8A, 0x62)

#: Write order when applying a preset.
APPLY_ORDER: tuple[int, ...] = (0xDC, 0x14, 0x8A, 0x72, 0x12, 0x10, 0x62)


def normalized(values: dict[int, int]) -> list[tuple[int, int]]:
    """Return ``values`` as a list ordered by :data:`APPLY_ORDER`."""
    return [(code, values[code]) for code in APPLY_ORDER if code in values]
