"""Tests for the pure-Python ddcutil parsing helpers."""

from __future__ import annotations

from nitor import ddc


def test_parse_continuous():
    assert ddc.parse_brief("VCP 10 C 40 100") == (40, 100)


def test_parse_continuous_hex_encoded():
    assert ddc.parse_brief("VCP 62 CNC x00 x64 x00 x32") == (50, 100)


def test_parse_simple_hex_encoded():
    assert ddc.parse_brief("VCP 60 SNC x0f") == (15, None)


def test_parse_rejects_garbage():
    assert ddc.parse_brief("") == (None, None)
    assert ddc.parse_brief("Display 1") == (None, None)
    assert ddc.parse_brief("VCP 10 X nope nope") == (None, None)


def test_parse_rejects_out_of_range():
    assert ddc.parse_brief("VCP 10 C") == (None, None)


def test_format_value_units():
    assert ddc.format_value(0xAE, 16510) == "165.10 Hz"
    assert ddc.format_value(0xAC, 52128) == "521.3 kHz"
    assert ddc.format_value(0x10, 40) == "40"
    assert ddc.format_value(0x10, None) == "—"


def test_feature_table_is_consistent():
    for code, meta in ddc.FEATURES.items():
        assert meta.code == code
        assert meta.kind in {"slider", "enum", "readonly", "action"}
        assert meta.category in ddc.ORDER


def test_display_order_covers_every_feature():
    assert set(ddc.DISPLAY_ORDER) == set(ddc.FEATURES)


def test_actions_are_flagged():
    assert {0x04, 0x05, 0x08} == ddc.ACTIONS
    for code in ddc.ACTIONS:
        assert ddc.FEATURES[code].kind == "action"
