"""Multi-display helpers built on top of :mod:`nitor.ddc`.

``ddc`` keeps its historical module-level functions so the current UI keeps
working unchanged.  This module is the entry point for code that wants to talk
to every monitor: :func:`discover` returns one :class:`~nitor.ddc.Display`
object per display, each pinned to its own I2C bus.

Nothing here imports GTK, which keeps the discovery layer unit-testable.
"""

from __future__ import annotations

from .ddc import Display, DisplayInfo, list_displays

__all__ = [
    "Display",
    "DisplayInfo",
    "discover",
    "find",
    "first",
    "list_displays",
    "stable_key",
]


def discover() -> list[Display]:
    """Return one :class:`Display` per monitor, in ``ddcutil`` order."""
    return [Display(info) for info in list_displays()]


def first() -> Display | None:
    """Return the first display, or ``None`` when no monitor is connected."""
    displays = discover()
    return displays[0] if displays else None


def find(identifier: str | int, displays: list[Display] | None = None) -> Display | None:
    """Find a display by index, serial number, connector or label.

    ``displays`` defaults to a fresh :func:`discover` call.  The first match
    wins, so pass a stable identifier (serial or connector) when possible.
    """
    items = discover() if displays is None else displays
    if isinstance(identifier, int):
        return next((item for item in items if item.info.index == identifier), None)

    text = identifier.strip()
    if not text:
        return None
    folded = text.casefold()
    for item in items:
        info = item.info
        if folded in {
            info.serial.casefold(),
            info.connector.casefold(),
            info.label.casefold(),
        }:
            return item
    if text.isdigit():
        return next((item for item in items if item.info.index == int(text)), None)
    return None


def stable_key(info: DisplayInfo) -> str:
    """Return a key suitable for persisting per-display settings.

    Serial number is preferred, then the DRM connector, then the I2C bus.
    ``ddcutil`` display indices are not stable across replugs, so they are the
    last resort.
    """
    if info.serial:
        return f"serial:{info.serial}"
    if info.connector:
        return f"connector:{info.connector}"
    if info.bus:
        return f"bus:{info.bus}"
    return f"index:{info.index}"
