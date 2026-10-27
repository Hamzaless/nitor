"""Tests for feature search."""

from __future__ import annotations

from nitor import search

NAMES = {
    0x10: "Brightness",
    0x12: "Contrast",
    0x1A: "Blue Gain",
    0x62: "Volume",
}


def test_normalize_query():
    assert search.normalize_query("  Bright\t") == "bright"
    assert search.normalize_query("") == ""
    assert search.normalize_query(None) == ""  # type: ignore[arg-type]


def test_empty_query_returns_every_code_in_order():
    assert search.filter_codes("", NAMES) == [0x10, 0x12, 0x1A, 0x62]
    assert search.filter_codes("   ", NAMES) == [0x10, 0x12, 0x1A, 0x62]


def test_name_substring_is_case_insensitive():
    assert search.filter_codes("BRIGHT", NAMES) == [0x10]
    assert search.filter_codes(" coNt ", NAMES) == [0x12]
    assert search.filter_codes("gain", NAMES) == [0x1A]


def test_hex_prefix():
    assert search.filter_codes("0x10", NAMES) == [0x10]
    assert search.filter_codes("0X62", NAMES) == [0x62]


def test_bare_hex():
    assert search.filter_codes("1a", NAMES) == [0x1A]
    assert search.filter_codes("1A", NAMES) == [0x1A]


def test_decimal_code():
    assert search.filter_codes("98", NAMES) == [0x62]
    assert search.filter_codes("16", NAMES) == [0x10]


def test_decimal_and_hex_both_match_ambiguous_digits():
    names = {0x10: "Brightness", 10: "Custom"}
    assert search.filter_codes("10", names) == [10, 0x10]


def test_no_match_returns_empty():
    assert search.filter_codes("firmware", NAMES) == []
    assert search.filter_codes("0xff", NAMES) == []


def test_unknown_hex_query_is_not_an_error():
    assert search.filter_codes("0xzz", NAMES) == []
