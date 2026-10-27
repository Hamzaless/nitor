"""Monitor details gathered from sysfs, EDID and DDC/CI.

This module deliberately has no GTK dependency.  The models it returns can be
used by the diagnostics dialog, by tests and by future command line tools.
Missing files and unreadable DDC features are normal: a monitor may be
disconnected, may not expose EDID, or may not implement an optional VCP code.
In that case the corresponding field is left empty instead of being guessed.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import ddc

__all__ = ["collect", "format_usage"]

#: Where the kernel exposes DRM connectors.
SYSFS_DRM = "/sys/class/drm"

#: Optional information features read through DDC/CI.
_DDC_CODES = (0xB6, 0xC9, 0xC0, 0xDF)

_RESOLUTION_RE = re.compile(r"^(\d{2,5})\s*[xX]\s*(\d{2,5})$")

#: EDID manufacturer IDs are three letters; expand the common ones.
_MANUFACTURER_NAMES = {
    "AUS": "ASUS",
    "ACR": "Acer",
    "AOC": "AOC",
    "BNQ": "BenQ",
    "DEL": "Dell",
    "GBT": "Gigabyte",
    "GSM": "LG",
    "HWP": "HP",
    "LEN": "Lenovo",
    "MSI": "MSI",
    "PHL": "Philips",
    "SAM": "Samsung",
    "VSC": "ViewSonic",
}

#: Brands that can be recognised in a ``"Dell U2720Q"`` style label.
_LABEL_BRANDS = {name.lower(): name for name in _MANUFACTURER_NAMES.values()}


def _read_text(path: str) -> str:
    """Return the contents of *path*, or an empty string when unavailable."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_bytes(path: str) -> bytes:
    """Return the raw contents of *path*, or ``b""`` when unavailable."""
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


def _manufacturer_name(raw: bytes) -> str:
    if len(raw) != 2:
        return ""
    value = int.from_bytes(raw, "big")
    letters = "".join(chr(((value >> shift) & 0x1F) + 64) for shift in (10, 5, 0))
    if not (len(letters) == 3 and letters.isalpha()):
        return ""
    return _MANUFACTURER_NAMES.get(letters, letters)


def _descriptor(data: bytes, tag: int) -> str:
    """Return a string descriptor (product name, serial, ...) from EDID."""
    for offset in (54, 72, 90, 108):
        block = data[offset : offset + 18]
        if len(block) < 18 or block[:3] != b"\x00\x00\x00" or block[3] != tag:
            continue
        text = block[5:18].split(b"\n", 1)[0].split(b"\x00", 1)[0]
        text = text.decode("ascii", "ignore").strip()
        if text:
            return text
    return ""


def _parse_edid(data: bytes) -> dict[str, str]:
    """Extract manufacturer, model and serial from an EDID blob.

    Malformed or absent EDID simply produces an empty mapping.
    """
    if len(data) < 128 or not data.startswith(b"\x00\xff\xff\xff\xff\xff\xff\x00"):
        return {}

    result: dict[str, str] = {}
    manufacturer = _manufacturer_name(data[8:10])
    if manufacturer:
        result["manufacturer"] = manufacturer

    model = _descriptor(data, 0xFC)
    if model:
        result["model"] = model

    serial = _descriptor(data, 0xFF)
    if not serial:
        numeric = int.from_bytes(data[12:16], "little")
        if numeric not in (0, 0xFFFFFFFF):
            serial = str(numeric)
    if serial:
        result["serial"] = serial
    return result


def _label_parts(label: str) -> tuple[str, str]:
    """Split ``"Dell U2720Q"`` into manufacturer and model when possible."""
    text = (label or "").strip()
    if not text:
        return "", ""
    first, _, rest = text.partition(" ")
    manufacturer = _LABEL_BRANDS.get(first.lower())
    if manufacturer and rest.strip():
        return manufacturer, rest.strip()
    return "", text


def _parse_resolution(line: str) -> str:
    match = _RESOLUTION_RE.match((line or "").strip())
    if not match:
        return ""
    return f"{int(match.group(1))}x{int(match.group(2))}"


def _mode_lines(connector: str) -> list[str]:
    text = _read_text(f"{SYSFS_DRM}/{connector}/modes")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _refresh_rate(connector: str) -> int | float | str:
    text = _read_text(f"{SYSFS_DRM}/{connector}/refresh").strip()
    if not text:
        return ""
    try:
        value = float(text)
    except ValueError:
        return ""
    if value <= 0:
        return ""
    return int(value) if value.is_integer() else value


def _current(code: int) -> int | None:
    """Read one VCP code and return its current value."""
    try:
        current, _maximum = ddc.get(code)
    except Exception:
        return None
    return current if isinstance(current, int) else None


def _ddcutil_version() -> str:
    try:
        version = ddc.ddcutil_version()
    except Exception:
        return ""
    return version if isinstance(version, str) else ""


def format_usage(minutes: int | None) -> str:
    """Format a usage counter as ``"12 h 34 min"``.

    ``None`` is rendered as an empty string so callers can choose their own
    placeholder.
    """
    if minutes is None:
        return ""
    try:
        total = max(0, int(minutes))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(total, 60)
    return f"{hours} h {remainder} min"


def collect(connector: str, label: str, bus: str | None = None) -> dict:
    """Collect everything Nitor knows about one monitor.

    *connector* is a DRM connector name such as ``"card0-DP-1"`` and *label*
    is the human readable name shown by the UI.  *bus* is accepted for callers
    that already discovered an I2C bus; DDC reads use ddcutil's cached bus.

    The DDC-backed fields keep the raw VCP current value.  Turning them into
    human readable text is the job of :mod:`nitor.report`, which keeps this
    model free of presentation decisions.
    """
    connector = (connector or "").strip()
    modes = _mode_lines(connector) if connector else []
    status = _read_text(f"{SYSFS_DRM}/{connector}/status").strip() if connector else ""
    refresh_hz = _refresh_rate(connector) if connector else ""

    edid = _parse_edid(_read_bytes(f"{SYSFS_DRM}/{connector}/edid")) if connector else {}
    manufacturer, model = _label_parts(label)
    manufacturer = edid.get("manufacturer", manufacturer)
    model = edid.get("model", model)

    return {
        "model": model,
        "manufacturer": manufacturer,
        "connector": connector,
        "serial": edid.get("serial", ""),
        "resolution": _parse_resolution(modes[0]) if modes else "",
        "refresh_hz": refresh_hz,
        "status": status,
        "modes": modes,
        "panel_type": _current(0xB6),
        "firmware": _current(0xC9),
        "usage_minutes": _current(0xC0),
        "vcp_version": _current(0xDF),
        "ddcutil_version": _ddcutil_version(),
    }
