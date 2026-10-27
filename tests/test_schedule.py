"""Tests for the pure scheduling helper."""

from __future__ import annotations

from datetime import datetime

from nitor.schedule import due_preset, parse_time

# Tuesday, 27 October 2026 (Python weekday 1).
NOW = datetime(2026, 10, 27, 20, 0)


def test_parse_time_accepts_hh_mm():
    assert parse_time("20:00") == 20 * 60
    assert parse_time("7:05") == 7 * 60 + 5
    assert parse_time("23:59") == 23 * 60 + 59


def test_parse_time_rejects_garbage():
    assert parse_time("24:00") is None
    assert parse_time("20:60") is None
    assert parse_time("2000") is None
    assert parse_time("") is None
    assert parse_time(2000) is None


def test_not_due_before_time():
    entries = [{"time": "20:01", "preset": "Gece"}]
    assert due_preset(NOW, entries, {}) is None


def test_due_exactly_at_time_and_after():
    entries = [{"time": "20:00", "preset": "Gece"}]
    assert due_preset(NOW, entries, {}) == "Gece"
    assert due_preset(datetime(2026, 10, 27, 23, 30), entries, {}) == "Gece"


def test_days_are_checked_when_given():
    entries = [{"time": "20:00", "preset": "Gece", "days": [1, 2]}]
    assert due_preset(NOW, entries, {}) == "Gece"

    entries = [{"time": "20:00", "preset": "Gece", "days": [0, 6]}]
    assert due_preset(NOW, entries, {}) is None


def test_missing_or_empty_days_means_every_day():
    assert due_preset(NOW, [{"time": "20:00", "preset": "Gece"}], {}) == "Gece"
    assert due_preset(NOW, [{"time": "20:00", "preset": "Gece", "days": []}], {}) == "Gece"


def test_last_run_today_blocks_the_preset():
    entries = [{"time": "20:00", "preset": "Gece"}]
    assert due_preset(NOW, entries, {"Gece": "2026-10-27"}) is None


def test_last_run_on_another_day_allows_the_preset():
    entries = [{"time": "20:00", "preset": "Gece"}]
    assert due_preset(NOW, entries, {"Gece": "2026-10-26"}) == "Gece"


def test_last_run_is_tracked_per_preset():
    entries = [
        {"time": "20:00", "preset": "Gece"},
        {"time": "20:00", "preset": "Gunduz"},
    ]
    last_run = {"Gece": "2026-10-27"}
    assert due_preset(NOW, entries, last_run) == "Gunduz"


def test_first_due_entry_wins():
    entries = [
        {"time": "21:00", "preset": "Sonra"},
        {"time": "20:00", "preset": "Gece"},
        {"time": "19:00", "preset": "Once"},
    ]
    assert due_preset(NOW, entries, {}) == "Gece"


def test_malformed_entries_are_ignored():
    entries = [
        "not a dict",
        {"time": "nope", "preset": "A"},
        {"time": "20:00"},
        {"time": "20:00", "preset": "   "},
        {"time": "20:00", "preset": "Gece", "days": "monday"},
        {"time": "20:00", "preset": "Son", "days": [99]},
        {"time": "20:00", "preset": "Gecerli"},
    ]
    assert due_preset(NOW, entries, {}) == "Gecerli"


def test_last_run_accepts_nested_date_object():
    entries = [{"time": "20:00", "preset": "Gece"}]
    last_run = {"Gece": {"date": "2026-10-27"}}  # type: ignore[dict-item]
    assert due_preset(NOW, entries, last_run) is None
