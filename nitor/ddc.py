"""Thin wrapper around the ``ddcutil`` command line tool.

Nitor never talks to ``/dev/i2c-*`` itself: ``ddcutil`` handles the DDC/CI
protocol, display locking, retries and quirks.  Everything here is pure
Python and importable without GTK, which keeps it unit-testable.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

__all__ = [
    "DdcError",
    "FeatureMeta",
    "FEATURES",
    "ORDER",
    "DISPLAY_ORDER",
    "ACTIONS",
    "Monitor",
    "DisplayInfo",
    "Display",
    "available",
    "capabilities",
    "detect",
    "list_displays",
    "ddcutil_version",
    "format_value",
    "get",
    "read_many",
    "parse_brief",
    "set_value",
]


class DdcError(RuntimeError):
    """Raised when ``ddcutil`` cannot be executed at all."""


@dataclass(frozen=True)
class FeatureMeta:
    """Static description of a VCP feature Nitor knows how to render."""

    code: int
    key: str
    kind: str  # "slider" | "enum" | "readonly" | "action"
    category: str


#: Features we are willing to show.  Anything the monitor reports that is not
#: listed here (e.g. undocumented manufacturer specific codes) is ignored on
#: purpose: writing unknown VCP codes can reset picture settings or leave a
#: monitor in a weird state.
FEATURES: dict[int, FeatureMeta] = {
    # Image
    0x10: FeatureMeta(0x10, "feature.brightness", "slider", "category.image"),
    0x12: FeatureMeta(0x12, "feature.contrast", "slider", "category.image"),
    0x14: FeatureMeta(0x14, "feature.color_preset", "enum", "category.image"),
    0x16: FeatureMeta(0x16, "feature.red_gain", "slider", "category.image"),
    0x18: FeatureMeta(0x18, "feature.green_gain", "slider", "category.image"),
    0x1A: FeatureMeta(0x1A, "feature.blue_gain", "slider", "category.image"),
    0x72: FeatureMeta(0x72, "feature.gamma", "slider", "category.image"),
    0x86: FeatureMeta(0x86, "feature.scaling", "enum", "category.image"),
    0x8A: FeatureMeta(0x8A, "feature.saturation", "slider", "category.image"),
    0xDC: FeatureMeta(0xDC, "feature.display_mode", "enum", "category.image"),
    # Audio
    0x62: FeatureMeta(0x62, "feature.volume", "slider", "category.audio"),
    0x8D: FeatureMeta(0x8D, "feature.screen_blank", "enum", "category.audio"),
    # Input and power
    0x60: FeatureMeta(0x60, "feature.input_source", "enum", "category.input"),
    0xD6: FeatureMeta(0xD6, "feature.power_mode", "enum", "category.input"),
    # On screen display
    0xCC: FeatureMeta(0xCC, "feature.osd_language", "enum", "category.osd"),
    # Read only information
    0xAC: FeatureMeta(0xAC, "feature.horizontal_freq", "readonly", "category.info"),
    0xAE: FeatureMeta(0xAE, "feature.vertical_freq", "readonly", "category.info"),
    0xB6: FeatureMeta(0xB6, "feature.panel_technology", "readonly", "category.info"),
    0xC0: FeatureMeta(0xC0, "feature.usage_time", "readonly", "category.info"),
    0xC6: FeatureMeta(0xC6, "feature.app_key", "readonly", "category.info"),
    0xC8: FeatureMeta(0xC8, "feature.controller", "readonly", "category.info"),
    0xC9: FeatureMeta(0xC9, "feature.firmware", "readonly", "category.info"),
    0xDF: FeatureMeta(0xDF, "feature.vcp_version", "readonly", "category.info"),
    # Actions
    0x04: FeatureMeta(0x04, "feature.restore_factory", "action", "category.actions"),
    0x05: FeatureMeta(0x05, "feature.restore_brightness_contrast", "action", "category.actions"),
    0x08: FeatureMeta(0x08, "feature.restore_color", "action", "category.actions"),
}

#: Display order inside a category.
DISPLAY_ORDER: list[int] = [
    0x10, 0x12, 0x14, 0x16, 0x18, 0x1A, 0x72, 0x86, 0x8A, 0xDC,
    0x62, 0x8D,
    0x60, 0xD6,
    0xCC,
    0xAC, 0xAE, 0xB6, 0xC0, 0xC6, 0xC8, 0xC9, 0xDF,
    0x04, 0x05, 0x08,
]

#: Category display order.
ORDER: list[str] = [
    "category.image",
    "category.audio",
    "category.input",
    "category.osd",
    "category.info",
    "category.actions",
]

#: VCP action codes (write triggers, no value semantics).
ACTIONS = {0x04, 0x05, 0x08}

_FEATURE_RE = re.compile(r"^\s{3}Feature:\s+([0-9A-Fa-f]{2})\s+\((.*?)\)\s*$")
_VALUE_RE = re.compile(r"^\s+([0-9A-Fa-f]{2}):\s*(.*?)\s*$")
_DISPLAY_RE = re.compile(r"^Display\s+(\d+)\s*$")
_MODEL_RE = re.compile(r"^\s+Model:\s+(.+?)\s*$")
_MFG_RE = re.compile(r"^\s+Mfg id:\s+(\S+)")
_CONNECTOR_RE = re.compile(r"^\s+DRM_connector:\s+(.+?)\s*$")
_BUS_RE = re.compile(r"^\s+I2C bus:\s+/dev/i2c-(\d+)")
_SERIAL_RE = re.compile(r"^\s+Serial number:\s+(.+?)\s*$")

#: I2C bus discovered by :func:`detect`; reused by every later call so ddcutil
#: does not have to scan for displays again (roughly twice as fast).
_BUS: str | None = None


class _GlobalBus:
    """Sentinel: ``_run`` should use the cached module-level :data:`_BUS`."""

    __slots__ = ()


_GLOBAL_BUS = _GlobalBus()

#: An explicit bus, ``None`` (let ddcutil pick), or :data:`_GLOBAL_BUS`.
_BusTarget = str | None | _GlobalBus


#: EDID manufacturer IDs are three letters; expand the common ones.
_MFG_NAMES = {
    "AUS": "ASUS", "ACR": "Acer", "AOC": "AOC", "BNQ": "BenQ", "DEL": "Dell",
    "GBT": "Gigabyte", "GSM": "LG", "HWP": "HP", "LEN": "Lenovo", "MSI": "MSI",
    "PHL": "Philips", "SAM": "Samsung", "VSC": "ViewSonic",
}


@dataclass(frozen=True)
class Monitor:
    index: int
    model: str
    manufacturer: str
    connector: str

    @property
    def label(self) -> str:
        return _display_label(self.manufacturer, self.model, self.connector, self.index)


@dataclass(frozen=True)
class DisplayInfo:
    """Everything Nitor needs to address and describe one physical display."""

    index: int
    bus: str | None
    connector: str
    manufacturer: str
    model: str
    serial: str
    label: str


def _display_label(manufacturer: str, model: str, connector: str, index: int) -> str:
    name = _MFG_NAMES.get(manufacturer, manufacturer)
    if name and name not in model:
        return f"{name} {model}".strip()
    return model or connector or f"Display {index}"


def available() -> bool:
    """Return True when ``ddcutil`` is on PATH."""
    return shutil.which("ddcutil") is not None


def _run(
    *args: str,
    timeout: float = 20.0,
    use_bus: bool = True,
    bus: _BusTarget = _GLOBAL_BUS,
) -> subprocess.CompletedProcess[str]:
    """Run ``ddcutil`` and capture its output.

    ``bus`` defaults to :data:`_GLOBAL_BUS`, which reuses the bus discovered by
    :func:`detect`.  Passing an explicit bus (or ``None``) bypasses that cache,
    which is what :class:`Display` does so displays never fight over ``_BUS``.
    """
    if not available():
        raise DdcError("ddcutil is not installed")
    target = _BUS if isinstance(bus, _GlobalBus) else bus
    command = ["ddcutil"]
    if use_bus and target is not None:
        command += ["--bus", target]
    command += list(args)
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - platform
        raise DdcError(str(exc)) from exc


def parse_brief(line: str) -> tuple[int | None, int | None]:
    """Parse one ``ddcutil getvcp --brief`` line into ``(current, maximum)``.

    Observed shapes::

        VCP 10 C 40 100                  continuous
        VCP 62 CNC x00 x64 x00 x32       continuous, hex encoded
        VCP 60 SNC x0f                   simple, hex encoded
    """
    parts = line.split()
    if len(parts) < 4 or parts[0] != "VCP":
        return None, None
    kind = parts[2]
    try:
        if kind in {"C", "NC"}:
            return int(parts[3]), int(parts[4])
        if kind.startswith("CNC"):
            return int(parts[6].lstrip("xX"), 16), int(parts[4].lstrip("xX"), 16)
        if kind.startswith("SNC"):
            return int(parts[3].lstrip("xX"), 16), None
    except (IndexError, ValueError):
        pass
    return None, None


def _get(
    bus: _BusTarget,
    code: int,
    *,
    retries: int = 2,
) -> tuple[int | None, int | None]:
    result: tuple[int | None, int | None] = (None, None)
    for _ in range(max(1, retries)):
        response = _run("getvcp", f"0x{code:02X}", "--brief", bus=bus)
        lines = response.stdout.strip().splitlines()
        if not lines:
            continue
        current, maximum = parse_brief(lines[-1])
        result = (current, maximum)
        if current is not None:
            break
    return result


def get(code: int, *, retries: int = 2) -> tuple[int | None, int | None]:
    """Read ``code`` from the first detected monitor.

    Returns ``(current, maximum)``; ``(None, None)`` when the feature is not
    readable.  DDC/CI is flaky by nature, hence the retry.
    """
    return _get(_GLOBAL_BUS, code, retries=retries)


def _read_many(
    bus: _BusTarget,
    codes: list[int],
    *,
    retries: int = 1,
) -> dict[int, tuple[int | None, int | None]]:
    unique = list(dict.fromkeys(codes))
    result: dict[int, tuple[int | None, int | None]] = {code: (None, None) for code in unique}
    if not unique:
        return result

    for _ in range(max(1, retries)):
        response = _run(
            "getvcp",
            *[f"0x{code:02X}" for code in unique],
            "--brief",
            timeout=40.0,
            bus=bus,
        )
        for line in response.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[0] != "VCP":
                continue
            try:
                code = int(parts[1], 16)
            except ValueError:
                continue
            if code in result:
                result[code] = parse_brief(line)
        if any(value[0] is not None for value in result.values()):
            break
    return result


def read_many(
    codes: list[int],
    *,
    retries: int = 1,
) -> dict[int, tuple[int | None, int | None]]:
    """Read many VCP codes in a single ``ddcutil`` invocation.

    ``ddcutil getvcp`` accepts several feature codes at once, which turns the
    startup probe from one process per row into a single call.
    """
    return _read_many(_GLOBAL_BUS, codes, retries=retries)


def _set_value(
    bus: _BusTarget,
    code: int,
    value: int,
    *,
    retries: int = 2,
    sleep_multiplier: str = "0.1",
) -> bool:
    for _ in range(max(1, retries)):
        try:
            response = _run(
                "--sleep-multiplier",
                sleep_multiplier,
                "setvcp",
                f"0x{code:02X}",
                str(value),
                bus=bus,
            )
        except DdcError:
            return False
        if response.returncode == 0:
            return True
    return False


def set_value(
    code: int,
    value: int,
    *,
    retries: int = 2,
    sleep_multiplier: str = "0.1",
) -> bool:
    """Write ``value`` to ``code``.  Returns True on a clean exit.

    ``sleep_multiplier`` is passed to ddcutil; lower values make bulk writes
    (for example applying a preset) considerably faster.
    """
    return _set_value(
        _GLOBAL_BUS,
        code,
        value,
        retries=retries,
        sleep_multiplier=sleep_multiplier,
    )


def _parse_capabilities(text: str) -> dict[int, dict[int, str]]:
    result: dict[int, dict[int, str]] = {}
    current: int | None = None

    for line in text.splitlines():
        match = _FEATURE_RE.match(line)
        if match:
            current = int(match.group(1), 16)
            result.setdefault(current, {})
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("Values:"):
            for token in re.findall(r"\b[0-9A-Fa-f]{2}\b", stripped[7:]):
                result[current].setdefault(int(token, 16), f"0x{token}")
            continue
        value_match = _VALUE_RE.match(line)
        if value_match:
            value = int(value_match.group(1), 16)
            label = value_match.group(2).strip() or f"0x{value:02X}"
            result[current][value] = label
    return result


def _capabilities(bus: _BusTarget = _GLOBAL_BUS) -> dict[int, dict[int, str]]:
    response = _run("capabilities", timeout=25.0, bus=bus)
    return _parse_capabilities(response.stdout)


def capabilities() -> dict[int, dict[int, str]]:
    """Return ``{code: {raw_value: label}}`` from the capabilities string."""
    return _capabilities()


def list_displays() -> list[DisplayInfo]:
    """Return every display ``ddcutil detect`` can see, in ddcutil order.

    Unlike :func:`detect` this keeps the I2C bus and serial number and never
    touches the module-level bus cache, so it is safe to call while other
    displays are in use.
    """
    response = _run("detect", timeout=25.0, use_bus=False)
    displays: list[DisplayInfo] = []
    index: int | None = None
    bus: str | None = None
    model = manufacturer = connector = serial = ""

    def flush() -> None:
        nonlocal index, bus, model, manufacturer, connector, serial
        if index is not None:
            displays.append(
                DisplayInfo(
                    index=index,
                    bus=bus,
                    connector=connector,
                    manufacturer=manufacturer,
                    model=model,
                    serial=serial,
                    label=_display_label(manufacturer, model, connector, index),
                )
            )
        index, bus, model, manufacturer, connector, serial = None, None, "", "", "", ""

    for line in response.stdout.splitlines():
        display_match = _DISPLAY_RE.match(line)
        if display_match:
            flush()
            index = int(display_match.group(1))
            continue
        if index is None:
            continue
        if match := _MODEL_RE.match(line):
            model = match.group(1)
        elif match := _MFG_RE.match(line):
            manufacturer = match.group(1)
        elif match := _SERIAL_RE.match(line):
            serial = match.group(1)
        elif match := _CONNECTOR_RE.match(line):
            connector = match.group(1)
        elif match := _BUS_RE.match(line):
            bus = match.group(1)
    flush()
    return displays


def detect() -> list[Monitor]:
    """Return the monitors ``ddcutil`` can see.

    Kept for the existing UI; :func:`list_displays` returns the richer
    :class:`DisplayInfo` records.  ``_BUS`` is pinned to the first display so
    the module-level functions keep addressing display 1.
    """
    global _BUS
    displays = list_displays()
    if displays and displays[0].bus is not None:
        _BUS = displays[0].bus
    return [
        Monitor(info.index, info.model, info.manufacturer, info.connector)
        for info in displays
    ]


class Display:
    """One physical monitor addressed by its own I2C bus.

    Unlike the module-level functions, an instance never reads or writes
    :data:`_BUS`: each call passes this display's own ``--bus`` so several
    instances can be used side by side without interfering.
    """

    def __init__(self, info: DisplayInfo) -> None:
        self.info = info

    def __repr__(self) -> str:
        return (
            f"Display(index={self.info.index}, "
            f"label={self.info.label!r}, bus={self.info.bus!r})"
        )

    def capabilities(self) -> dict[int, dict[int, str]]:
        """Return ``{code: {raw_value: label}}`` for this display."""
        return _capabilities(self.info.bus)

    def read_many(
        self,
        codes: list[int],
        *,
        retries: int = 1,
    ) -> dict[int, tuple[int | None, int | None]]:
        """Read many VCP codes from this display in one invocation."""
        return _read_many(self.info.bus, codes, retries=retries)

    def get(self, code: int, *, retries: int = 2) -> tuple[int | None, int | None]:
        """Read ``code`` from this display as ``(current, maximum)``."""
        return _get(self.info.bus, code, retries=retries)

    def set_value(
        self,
        code: int,
        value: int,
        sleep_multiplier: str = "0.1",
        *,
        retries: int = 2,
    ) -> bool:
        """Write ``value`` to ``code`` on this display."""
        return _set_value(
            self.info.bus,
            code,
            value,
            retries=retries,
            sleep_multiplier=sleep_multiplier,
        )


def ddcutil_version() -> str:
    """Return the version line reported by ddcutil, or an empty string."""
    try:
        response = _run("--version", use_bus=False)
    except DdcError:
        return ""
    lines = response.stdout.strip().splitlines()
    return lines[0].strip() if lines else ""


def format_value(code: int, value: int | None) -> str:
    """Format a raw VCP value for read-only rows."""
    if value is None:
        return "—"
    if code == 0xAE:
        return f"{value / 100:.2f} Hz"
    if code == 0xAC:
        return f"{value / 100:.1f} kHz"
    return str(value)
