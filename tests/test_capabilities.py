"""Tests for capabilities and detect parsing (no monitor required)."""

from __future__ import annotations

from types import SimpleNamespace

from nitor import ddc

CAPABILITIES = """Model: VG279QL1A
MCCS version: 2.2
VCP Features:
   Feature: 02 (New control value)
   Feature: 10 (Brightness)
   Feature: 60 (Input Source)
      Values:
         11: HDMI-1
         0f: DisplayPort-1
   Feature: 8D (Audio mute/Screen blank)
      Values: 01 02 (interpretation unavailable)
   Feature: E2 (Manufacturer specific feature)
      Values: 00 01 02 03 04 05 (interpretation unavailable)
"""

DETECT = """Display 1
   I2C bus:  /dev/i2c-1
   DRM_connector:           card0-DP-1
   EDID synopsis:
      Mfg id:               AUS
      Model:                VG279QL1A
"""


def test_capabilities_parsing(monkeypatch):
    monkeypatch.setattr(ddc, "_run", lambda *a, **k: SimpleNamespace(stdout=CAPABILITIES, returncode=0))
    caps = ddc.capabilities()

    assert set(caps) == {0x02, 0x10, 0x60, 0x8D, 0xE2}
    assert caps[0x60] == {0x11: "HDMI-1", 0x0F: "DisplayPort-1"}
    assert caps[0x8D] == {0x01: "0x01", 0x02: "0x02"}
    assert 0xE2 in caps  # parsed, but the UI deliberately ignores unknown codes


def test_detect_parsing(monkeypatch):
    monkeypatch.setattr(ddc, "_run", lambda *a, **k: SimpleNamespace(stdout=DETECT, returncode=0))
    monitors = ddc.detect()

    assert len(monitors) == 1
    monitor = monitors[0]
    assert monitor.index == 1
    assert monitor.model == "VG279QL1A"
    assert monitor.manufacturer == "AUS"
    assert monitor.connector == "card0-DP-1"
    assert monitor.label == "ASUS VG279QL1A"


def test_detect_without_monitor(monkeypatch):
    monkeypatch.setattr(
        ddc, "_run", lambda *a, **k: SimpleNamespace(stdout="", returncode=0)
    )
    assert ddc.detect() == []
