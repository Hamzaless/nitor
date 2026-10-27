"""Tests for built-in presets and preset management."""

from __future__ import annotations

import json

import pytest

from nitor import preset_io, presets

SAMPLE = {
    "day": {"16": 80, "18": 50},
    "night": {"16": 20, "18": 40},
}


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


# --- validate_name ---------------------------------------------------------


def test_validate_name_strips_whitespace():
    assert presets.validate_name("  desk  ") == "desk"


@pytest.mark.parametrize("name", ["", "   ", "x" * 61, None, 5])
def test_validate_name_rejects_bad_names(name):
    with pytest.raises(ValueError):
        presets.validate_name(name)


def test_validate_name_accepts_limit():
    assert presets.validate_name("x" * 60) == "x" * 60


# --- rename_preset ---------------------------------------------------------


def test_rename_preserves_order_and_does_not_mutate():
    original = {"a": {"16": 1}, "b": {"16": 2}, "c": {"16": 3}}
    renamed = presets.rename_preset(original, "b", "  middle  ")
    assert list(renamed) == ["a", "middle", "c"]
    assert renamed["middle"] == {"16": 2}
    assert list(original) == ["a", "b", "c"]
    assert renamed is not original


def test_rename_to_same_name_is_a_copy():
    original = {"a": {"16": 1}}
    renamed = presets.rename_preset(original, "a", " a ")
    assert renamed == original
    assert renamed is not original


def test_rename_missing_raises_key_error():
    with pytest.raises(KeyError):
        presets.rename_preset(SAMPLE, "missing", "new")


def test_rename_conflict_raises_value_error():
    with pytest.raises(ValueError):
        presets.rename_preset(SAMPLE, "day", "night")


def test_rename_invalid_name_raises_value_error():
    with pytest.raises(ValueError):
        presets.rename_preset(SAMPLE, "day", "   ")


# --- duplicate_preset ------------------------------------------------------


def test_duplicate_uses_copy_suffix():
    duplicated = presets.duplicate_preset(SAMPLE, "day")
    assert list(duplicated) == ["day", "night", "day copy"]
    assert duplicated["day copy"] == SAMPLE["day"]
    assert duplicated["day copy"] is not SAMPLE["day"]


def test_duplicate_adds_unique_numbered_suffix():
    once = presets.duplicate_preset(SAMPLE, "day")
    twice = presets.duplicate_preset(once, "day")
    assert "day copy 2" in twice
    assert "day copy" in twice


def test_duplicate_with_explicit_name():
    duplicated = presets.duplicate_preset(SAMPLE, "day", "bright")
    assert list(duplicated) == ["day", "night", "bright"]
    assert duplicated["bright"] == SAMPLE["day"]


def test_duplicate_explicit_conflict_raises_value_error():
    with pytest.raises(ValueError):
        presets.duplicate_preset(SAMPLE, "day", "night")


def test_duplicate_missing_raises_key_error():
    with pytest.raises(KeyError):
        presets.duplicate_preset(SAMPLE, "missing")


def test_duplicate_keeps_long_names_within_limit():
    long_name = "n" * 60
    source = {long_name: {"16": 1}}
    duplicated = presets.duplicate_preset(source, long_name)
    copy_name = next(name for name in duplicated if name != long_name)
    assert len(copy_name) <= 60
    assert copy_name != long_name


# --- delete_preset ---------------------------------------------------------


def test_delete_removes_preset_and_returns_copy():
    deleted = presets.delete_preset(SAMPLE, "night")
    assert deleted == {"day": SAMPLE["day"]}
    assert "night" in SAMPLE
    assert deleted is not SAMPLE


def test_delete_missing_raises_key_error():
    with pytest.raises(KeyError):
        presets.delete_preset(SAMPLE, "missing")


# --- sort_presets ----------------------------------------------------------


def test_sort_reorders_and_keeps_unknown_at_end():
    ordered = presets.sort_presets(SAMPLE, ["night", "unknown", "day"])
    assert list(ordered) == ["night", "day"]
    assert ordered == SAMPLE


def test_sort_keeps_unnamed_and_ignores_duplicates():
    ordered = presets.sort_presets(SAMPLE, ["night", "night"])
    assert list(ordered) == ["night", "day"]


def test_sort_does_not_mutate_original():
    presets.sort_presets(SAMPLE, ["night"])
    assert list(SAMPLE) == ["day", "night"]


# --- diff_preset -----------------------------------------------------------


def test_diff_reports_changed_and_new_codes():
    current = {0x10: 50, 0x12: 60, 0x14: 5}
    target = {0x10: 80, 0x12: 60, 0x8A: 10}
    changes = presets.diff_preset(current, target)
    assert changes == {0x8A: (None, 10), 0x10: (50, 80)}


def test_diff_is_empty_for_matching_values():
    assert presets.diff_preset({0x10: 50}, {0x10: 50}) == {}


def test_diff_ignores_codes_absent_from_target():
    assert presets.diff_preset({0x10: 50}, {}) == {}


# --- export_json / import_json --------------------------------------------


def test_export_is_versioned_and_sorted():
    document = json.loads(preset_io.export_json({"night": {"18": 40}, "day": {"18": 2, "16": 80}}))
    assert document["format"] == 1
    assert list(document["presets"]) == ["day", "night"]
    assert list(document["presets"]["day"]) == ["16", "18"]


def test_export_keeps_unicode_readable():
    text = preset_io.export_json({"gece": {"16": 10}})
    assert '"gece"' in text
    assert "\\u" not in text


def test_export_import_roundtrip():
    duplicated = presets.duplicate_preset(SAMPLE, "day")
    restored = preset_io.import_json(preset_io.export_json(duplicated))
    assert restored == duplicated


def test_import_accepts_legacy_plain_dict():
    text = json.dumps({"day": {"16": 80, "18": 50}})
    assert preset_io.import_json(text) == {"day": {"16": 80, "18": 50}}


def test_import_accepts_legacy_integer_codes_and_hex_strings():
    text = json.dumps({"day": {"0x10": "80"}})
    assert preset_io.import_json(text) == {"day": {"16": 80}}


def test_import_trims_names():
    text = json.dumps({" day ": {"16": 80}})
    assert preset_io.import_json(text) == {"day": {"16": 80}}


def test_import_rejects_broken_json():
    with pytest.raises(ValueError, match="invalid preset JSON"):
        preset_io.import_json("{not json")


def test_import_rejects_non_object():
    with pytest.raises(ValueError, match="object at the top level"):
        preset_io.import_json("[]")


def test_import_rejects_unsupported_format():
    text = json.dumps({"format": 2, "presets": SAMPLE})
    with pytest.raises(ValueError, match="unsupported preset format"):
        preset_io.import_json(text)


def test_import_rejects_missing_presets_object():
    text = json.dumps({"format": 1, "presets": []})
    with pytest.raises(ValueError, match="presets"):
        preset_io.import_json(text)


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_import_rejects_bad_names(name):
    with pytest.raises(ValueError, match="preset name"):
        preset_io.import_json(json.dumps({name: {"16": 80}}))


def test_import_rejects_duplicate_names_after_trimming():
    text = json.dumps({"day": {"16": 80}, " day ": {"16": 20}})
    with pytest.raises(ValueError, match="duplicate preset name"):
        preset_io.import_json(text)


@pytest.mark.parametrize("code", [-1, 256, "x", None, True])
def test_import_rejects_bad_codes(code):
    with pytest.raises(ValueError, match="VCP code"):
        preset_io.import_json(json.dumps({"day": {str(code): 10}}))


@pytest.mark.parametrize("value", [-1, 256, "x", None, True])
def test_import_rejects_bad_values(value):
    with pytest.raises(ValueError, match="value"):
        preset_io.import_json(json.dumps({"day": {"16": value}}))


def test_import_rejects_non_mapping_values():
    with pytest.raises(ValueError, match="mapping"):
        preset_io.import_json(json.dumps({"day": [80]}))


def test_export_rejects_out_of_range_values():
    with pytest.raises(ValueError, match="0-255"):
        preset_io.export_json({"day": {"16": 999}})


def test_export_rejects_non_mapping():
    with pytest.raises(ValueError, match="mapping"):
        preset_io.export_json([])
