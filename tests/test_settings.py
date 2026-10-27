"""Tests for persistent settings."""

from __future__ import annotations

import json

from nitor.settings import Settings, config_path


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = Settings.load()
    assert settings.language == "system"
    assert settings.auto_hide is True
    assert settings.debounce_ms == 150
    assert settings.presets == {}


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    original = Settings(
        language="tr",
        auto_hide=False,
        debounce_ms=500,
        presets={"desk": {"16": 40, "18": 80}},
    )
    original.save()
    assert config_path().is_file()

    loaded = Settings.load()
    assert loaded.language == "tr"
    assert loaded.auto_hide is False
    assert loaded.debounce_ms == 500
    assert loaded.presets == {"desk": {"16": 40, "18": 80}}


def test_broken_file_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert Settings.load().language == "system"


def test_values_are_sanitised(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "language": 5,
                "auto_hide": "yes",
                "debounce_ms": 99999,
                "presets": {"ok": {"16": 40}, "bad": {"x": "y"}, "empty": {}},
            }
        ),
        encoding="utf-8",
    )
    settings = Settings.load()
    assert settings.language == "system"
    assert settings.auto_hide is True
    assert settings.debounce_ms == 2000
    assert settings.presets == {"ok": {"16": 40}}
