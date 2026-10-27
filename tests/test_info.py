"""Tests for the pure-Python monitor details model."""

from __future__ import annotations

from nitor import ddc, info

EXPECTED_KEYS = {
    "model",
    "manufacturer",
    "connector",
    "serial",
    "resolution",
    "refresh_hz",
    "status",
    "modes",
    "panel_type",
    "firmware",
    "usage_minutes",
    "vcp_version",
    "ddcutil_version",
}


def _sysfs(name: str, connector: str = "card0-DP-1") -> str:
    return f"{info.SYSFS_DRM}/{connector}/{name}"


def _patch_sysfs(monkeypatch, files: dict[str, str]) -> None:
    monkeypatch.setattr(info, "_read_text", lambda path: files.get(path, ""))
    monkeypatch.setattr(info, "_read_bytes", lambda path: b"")


def _patch_ddc(monkeypatch, values: dict[int, tuple[int | None, int | None]]) -> None:
    monkeypatch.setattr(ddc, "get", lambda code: values.get(code, (None, None)))
    monkeypatch.setattr(ddc, "ddcutil_version", lambda: "ddcutil 2.1.4")


def test_collect_reads_sysfs_and_ddc(monkeypatch):
    _patch_sysfs(
        monkeypatch,
        {
            _sysfs("status"): "connected\n",
            _sysfs("modes"): "1920x1080\n1280x720\n1024x768\n",
            _sysfs("refresh"): "60\n",
        },
    )
    _patch_ddc(
        monkeypatch,
        {
            0xB6: (3, None),
            0xC9: (0x10, None),
            0xC0: (125, None),
            0xDF: (0x22, None),
        },
    )

    details = info.collect("card0-DP-1", "Dell U2720Q")

    assert set(details) == EXPECTED_KEYS
    assert details["model"] == "U2720Q"
    assert details["manufacturer"] == "Dell"
    assert details["connector"] == "card0-DP-1"
    assert details["serial"] == ""
    assert details["resolution"] == "1920x1080"
    assert details["refresh_hz"] == 60
    assert details["status"] == "connected"
    assert details["modes"] == ["1920x1080", "1280x720", "1024x768"]
    assert details["panel_type"] == 3
    assert details["firmware"] == 0x10
    assert details["usage_minutes"] == 125
    assert details["vcp_version"] == 0x22
    assert details["ddcutil_version"] == "ddcutil 2.1.4"


def test_collect_missing_files_returns_empty_values(monkeypatch):
    _patch_sysfs(monkeypatch, {})
    _patch_ddc(monkeypatch, {})

    details = info.collect("card0-DP-1", "Some Monitor", bus="7")

    assert details["resolution"] == ""
    assert details["refresh_hz"] == ""
    assert details["status"] == ""
    assert details["modes"] == []
    assert details["panel_type"] is None
    assert details["firmware"] is None
    assert details["usage_minutes"] is None
    assert details["vcp_version"] is None


def test_collect_tolerates_ddc_failures(monkeypatch):
    _patch_sysfs(monkeypatch, {})

    def broken(_code: int) -> tuple[int | None, int | None]:
        raise RuntimeError("ddcutil crashed")

    monkeypatch.setattr(ddc, "get", broken)
    monkeypatch.setattr(ddc, "ddcutil_version", lambda: "ddcutil 2.1.4")

    details = info.collect("card0-DP-1", "Dell U2720Q")

    assert details["panel_type"] is None
    assert details["firmware"] is None
    assert details["usage_minutes"] is None
    assert details["vcp_version"] is None
    assert details["ddcutil_version"] == "ddcutil 2.1.4"


def test_collect_without_connector_does_not_read_sysfs(monkeypatch):
    calls: list[str] = []

    def read(path: str) -> str:
        calls.append(path)
        return "connected\n"

    monkeypatch.setattr(info, "_read_text", read)
    monkeypatch.setattr(info, "_read_bytes", lambda path: b"")
    _patch_ddc(monkeypatch, {})

    details = info.collect("", "Display 1")

    assert calls == []
    assert details["connector"] == ""
    assert details["status"] == ""
    assert details["modes"] == []


def test_resolution_comes_from_the_first_mode(monkeypatch):
    _patch_sysfs(monkeypatch, {_sysfs("modes"): "2560x1440\n1920x1080\n"})
    _patch_ddc(monkeypatch, {})

    assert info.collect("card0-DP-1", "Monitor")["resolution"] == "2560x1440"


def test_malformed_mode_does_not_invent_a_resolution(monkeypatch):
    _patch_sysfs(monkeypatch, {_sysfs("modes"): "not a mode\n"})
    _patch_ddc(monkeypatch, {})

    details = info.collect("card0-DP-1", "Monitor")

    assert details["resolution"] == ""
    assert details["modes"] == ["not a mode"]


def test_refresh_accepts_decimal_values(monkeypatch):
    _patch_sysfs(monkeypatch, {_sysfs("refresh"): "59.94\n"})
    _patch_ddc(monkeypatch, {})

    assert info.collect("card0-DP-1", "Monitor")["refresh_hz"] == 59.94


def test_refresh_ignores_garbage(monkeypatch):
    _patch_sysfs(monkeypatch, {_sysfs("refresh"): "fast\n"})
    _patch_ddc(monkeypatch, {})

    assert info.collect("card0-DP-1", "Monitor")["refresh_hz"] == ""


def _edid() -> bytes:
    data = bytearray(128)
    data[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    data[8:10] = (0x10AC).to_bytes(2, "big")  # DEL -> Dell
    data[12:16] = (987654).to_bytes(4, "little")
    data[54:59] = b"\x00\x00\x00\xfc\x00"
    data[59:67] = b"U2720Q\n\x00"
    return bytes(data)


def test_parse_edid_extracts_identity():
    parsed = info._parse_edid(_edid())

    assert parsed["manufacturer"] == "Dell"
    assert parsed["model"] == "U2720Q"
    assert parsed["serial"] == "987654"


def test_parse_edid_rejects_garbage():
    assert info._parse_edid(b"") == {}
    assert info._parse_edid(b"not an edid") == {}


def test_collect_prefers_edid_identity(monkeypatch):
    _patch_sysfs(monkeypatch, {})
    monkeypatch.setattr(info, "_read_bytes", lambda path: _edid())
    _patch_ddc(monkeypatch, {})

    details = info.collect("card0-DP-1", "A different label")

    assert details["manufacturer"] == "Dell"
    assert details["model"] == "U2720Q"
    assert details["serial"] == "987654"


def test_format_usage():
    assert info.format_usage(None) == ""
    assert info.format_usage(0) == "0 h 0 min"
    assert info.format_usage(59) == "0 h 59 min"
    assert info.format_usage(125) == "2 h 5 min"
    assert info.format_usage(-5) == "0 h 0 min"
