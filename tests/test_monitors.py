"""Tests for multi-display discovery and per-display command construction."""

from __future__ import annotations

from types import SimpleNamespace

from nitor import ddc, monitors

DETECT_TWO = """Display 1
   I2C bus:  /dev/i2c-1
   DRM_connector:           card0-DP-1
   EDID synopsis:
      Mfg id:               AUS
      Model:                VG279QL1A
      Serial number:        H1LMQS123456
      Product code:         1234
      Binary serial number: 16843009 (0x01010101)
Display 2
   I2C bus:  /dev/i2c-3
   DRM_connector:           card0-HDMI-A-1
   EDID synopsis:
      Mfg id:               GSM
      Model:                27GL850
      Serial number:        0
"""

CAPABILITIES = """VCP Features:
   Feature: 10 (Brightness)
"""


def test_list_displays_parses_two_monitors(monkeypatch):
    monkeypatch.setattr(
        ddc, "_run", lambda *a, **k: SimpleNamespace(stdout=DETECT_TWO, returncode=0)
    )
    monkeypatch.setattr(ddc, "_BUS", "99")

    displays = ddc.list_displays()

    assert len(displays) == 2
    first, second = displays
    assert (first.index, first.bus, first.connector) == (1, "1", "card0-DP-1")
    assert (first.manufacturer, first.model, first.serial) == (
        "AUS",
        "VG279QL1A",
        "H1LMQS123456",
    )
    assert first.label == "ASUS VG279QL1A"

    assert (second.index, second.bus, second.connector) == (2, "3", "card0-HDMI-A-1")
    assert (second.manufacturer, second.model, second.serial) == ("GSM", "27GL850", "0")
    assert second.label == "LG 27GL850"

    # Discovery must not overwrite the bus cache used by the module-level API.
    assert ddc._BUS == "99"


def test_list_displays_without_monitor(monkeypatch):
    monkeypatch.setattr(ddc, "_run", lambda *a, **k: SimpleNamespace(stdout="", returncode=0))
    assert ddc.list_displays() == []


def test_detect_keeps_monitor_api_and_first_bus(monkeypatch):
    monkeypatch.setattr(
        ddc, "_run", lambda *a, **k: SimpleNamespace(stdout=DETECT_TWO, returncode=0)
    )
    monkeypatch.setattr(ddc, "_BUS", None)

    detected = ddc.detect()

    assert len(detected) == 2
    assert all(isinstance(monitor, ddc.Monitor) for monitor in detected)
    assert detected[0].label == "ASUS VG279QL1A"
    assert detected[1].connector == "card0-HDMI-A-1"
    assert ddc._BUS == "1"  # module-level helpers stay on display 1


def _fake_process(calls):
    def run(command, **_kwargs):
        calls.append(command)
        stdout = CAPABILITIES if "capabilities" in command else "VCP 10 C 40 100"
        return SimpleNamespace(stdout=stdout, returncode=0, stderr="")

    return run


def _info(index: int, bus: str | None, label: str) -> ddc.DisplayInfo:
    return ddc.DisplayInfo(
        index=index,
        bus=bus,
        connector=f"card0-DP-{index}",
        manufacturer="AUS",
        model=label,
        serial=str(index),
        label=label,
    )


def test_display_methods_use_their_own_bus(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ddc, "available", lambda: True)
    monkeypatch.setattr(ddc.subprocess, "run", _fake_process(calls))
    monkeypatch.setattr(ddc, "_BUS", "99")

    first = ddc.Display(_info(1, "1", "ASUS VG279QL1A"))
    second = ddc.Display(_info(2, "3", "LG 27GL850"))

    assert first.capabilities() == {0x10: {}}
    assert first.get(0x10) == (40, 100)
    assert first.read_many([0x10]) == {0x10: (40, 100)}
    assert first.set_value(0x10, 50)

    assert second.get(0x10) == (40, 100)
    assert second.set_value(0x10, 20, sleep_multiplier="0.05")

    assert calls[0] == ["ddcutil", "--bus", "1", "capabilities"]
    assert calls[1] == ["ddcutil", "--bus", "1", "getvcp", "0x10", "--brief"]
    assert calls[2] == ["ddcutil", "--bus", "1", "getvcp", "0x10", "--brief"]
    assert calls[3] == [
        "ddcutil",
        "--bus",
        "1",
        "--sleep-multiplier",
        "0.1",
        "setvcp",
        "0x10",
        "50",
    ]

    second_calls = calls[4:]
    assert second_calls[0] == ["ddcutil", "--bus", "3", "getvcp", "0x10", "--brief"]
    assert second_calls[1] == [
        "ddcutil",
        "--bus",
        "3",
        "--sleep-multiplier",
        "0.05",
        "setvcp",
        "0x10",
        "20",
    ]
    assert all("99" not in command for command in calls)
    assert ddc._BUS == "99"


def test_display_without_bus_does_not_use_the_cached_bus(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ddc, "available", lambda: True)
    monkeypatch.setattr(ddc.subprocess, "run", _fake_process(calls))
    monkeypatch.setattr(ddc, "_BUS", "99")

    display = ddc.Display(_info(2, None, "Unknown"))
    assert display.get(0x10) == (40, 100)

    assert calls == [["ddcutil", "getvcp", "0x10", "--brief"]]


def test_module_level_functions_still_use_cached_bus(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ddc, "available", lambda: True)
    monkeypatch.setattr(ddc.subprocess, "run", _fake_process(calls))
    monkeypatch.setattr(ddc, "_BUS", "7")

    assert ddc.read_many([0x10]) == {0x10: (40, 100)}

    assert calls == [["ddcutil", "--bus", "7", "getvcp", "0x10", "--brief"]]


def test_discover_and_find(monkeypatch):
    monkeypatch.setattr(
        ddc, "_run", lambda *a, **k: SimpleNamespace(stdout=DETECT_TWO, returncode=0)
    )

    displays = monitors.discover()

    assert len(displays) == 2
    assert all(isinstance(display, ddc.Display) for display in displays)
    assert monitors.first() is not None
    assert monitors.first().info.index == 1
    assert monitors.find(2, displays).info.label == "LG 27GL850"
    assert monitors.find("2", displays).info.index == 2
    assert monitors.find("H1LMQS123456", displays).info.index == 1
    assert monitors.find("card0-HDMI-A-1", displays).info.index == 2
    assert monitors.find("asus vg279ql1a", displays).info.index == 1
    assert monitors.find("missing", displays) is None
    assert monitors.find("", displays) is None


def test_stable_key_prefers_serial_then_connector_then_bus():
    assert monitors.stable_key(_info(1, "1", "A")) == "serial:1"
    assert (
        monitors.stable_key(
            ddc.DisplayInfo(1, "1", "card0-DP-1", "AUS", "X", "", "ASUS X")
        )
        == "connector:card0-DP-1"
    )
    assert (
        monitors.stable_key(ddc.DisplayInfo(1, "1", "", "AUS", "X", "", "ASUS X"))
        == "bus:1"
    )
    assert (
        monitors.stable_key(ddc.DisplayInfo(1, None, "", "AUS", "X", "", "ASUS X"))
        == "index:1"
    )
