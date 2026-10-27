"""Tests for the update checker (no network access)."""

from __future__ import annotations

from nitor import updates


def test_parse_version():
    assert updates.parse_version("1.2.3") == (1, 2, 3)
    assert updates.parse_version("v1.2.3") == (1, 2, 3)
    assert updates.parse_version("1.2.3-beta4") == (1, 2, 3, 4)
    assert updates.parse_version("") == (0,)


def test_is_newer():
    assert updates.is_newer("1.3.0", "1.2.1")
    assert updates.is_newer("1.10.0", "1.9.0")
    assert not updates.is_newer("1.2.1", "1.2.1")
    assert not updates.is_newer("1.2.0", "1.2.1")


def test_equal_lengths_are_padded():
    assert updates.is_newer("1.3", "1.2.9")
    assert not updates.is_newer("1.2", "1.2.9")
