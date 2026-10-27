"""Tests for built-in presets."""

from __future__ import annotations

from nitor import presets


def test_apply_order_puts_colour_before_brightness():
    values = {0x10: 50, 0x12: 60, 0x14: 0x05}
    ordered = presets.normalized(values)
    codes = [code for code, _value in ordered]
    assert codes.index(0x14) < codes.index(0x10)
    assert codes.index(0x12) < codes.index(0x10)


def test_normalized_drops_unknown_codes():
    values = {0x10: 50, 0x99: 1}
    assert presets.normalized(values) == [(0x10, 50)]


def test_capture_codes_are_bytes():
    assert all(0 <= code <= 0xFF for code in presets.CAPTURE_CODES)
