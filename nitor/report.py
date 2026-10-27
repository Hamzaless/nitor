"""Human readable diagnostics and prefilled GitHub issue links.

Like :mod:`nitor.info` this module is GTK free, so the text can be produced
from a worker thread and tested without a display server.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from . import info as monitor_info

__all__ = ["diagnostics_text", "issue_url"]

#: Where new issues are filed.
ISSUE_URL = "https://github.com/Hamzaless/nitor/issues/new"

#: A small subset of MCCS 0xB6 panel technology values.  Unknown values are
#: shown as hex rather than pretending to know the panel type.
_PANEL_NAMES = {
    0x01: "TN",
    0x02: "VA",
    0x03: "IPS",
}


def _text(value: object) -> str:
    """Render a value for diagnostics, using an em dash when it is missing."""
    if value is None:
        return "—"
    text = str(value).strip()
    return text or "—"


def _refresh_text(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _text(value)
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return f"{text} Hz"


def _panel_text(value: object) -> str:
    if isinstance(value, int):
        return _PANEL_NAMES.get(value, f"0x{value:02X}")
    return _text(value)


def _vcp_text(value: object) -> str:
    if isinstance(value, int):
        return f"{value >> 4}.{value & 0x0F}"
    return _text(value)


def _firmware_text(value: object) -> str:
    if isinstance(value, int):
        return f"0x{value:02X}"
    return _text(value)


def _modes_text(modes: object) -> str:
    if not isinstance(modes, (list, tuple)):
        return "—"
    text = ", ".join(str(mode).strip() for mode in modes if str(mode).strip())
    return text or "—"


def diagnostics_text(info: dict, version: str, session: dict) -> str:
    """Return a multiline report suitable for pasting into a bug report."""
    usage = monitor_info.format_usage(info.get("usage_minutes")) or "—"

    lines = [
        f"Nitor {_text(version)}",
        "",
        "Monitor",
        f"  Model: {_text(info.get('model'))}",
        f"  Manufacturer: {_text(info.get('manufacturer'))}",
        f"  Connector: {_text(info.get('connector'))}",
        f"  Serial: {_text(info.get('serial'))}",
        f"  Status: {_text(info.get('status'))}",
        f"  Resolution: {_text(info.get('resolution'))}",
        f"  Refresh rate: {_refresh_text(info.get('refresh_hz'))}",
        f"  Panel technology: {_panel_text(info.get('panel_type'))}",
        f"  Firmware: {_firmware_text(info.get('firmware'))}",
        f"  Usage time: {usage}",
        f"  VCP version: {_vcp_text(info.get('vcp_version'))}",
        f"  ddcutil: {_text(info.get('ddcutil_version'))}",
        f"  Modes: {_modes_text(info.get('modes'))}",
    ]

    if session:
        lines += ["", "Session"]
        lines += [f"  {key}: {_text(value)}" for key, value in session.items()]

    return "\n".join(lines)


def issue_url(title: str, body: str, labels: list[str] | None = None) -> str:
    """Build a prefilled ``/issues/new`` URL.

    Title, body and labels are URL encoded, so newlines, ``&``, ``#`` and
    non-ASCII characters survive the round trip through a browser.
    """
    params = {"title": str(title), "body": str(body)}
    selected = [label.strip() for label in (labels or []) if label and label.strip()]
    if selected:
        params["labels"] = ",".join(selected)
    return f"{ISSUE_URL}?{urlencode(params, quote_via=quote)}"
