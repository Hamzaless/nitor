"""Tests for the translation layer."""

from __future__ import annotations

from nitor import i18n


def test_english_is_the_fallback():
    assert i18n.init("en") == "en"
    assert i18n.translate("app.name") == "Nitor"
    assert i18n.translate("category.image") == "Image"


def test_turkish_translation():
    assert i18n.init("tr") == "tr"
    assert i18n.translate("category.image") == "Görüntü"
    assert i18n.translate("feature.brightness") == "Parlaklık"


def test_missing_key_is_returned_unchanged():
    i18n.init("en")
    assert i18n.translate("no.such.key") == "no.such.key"


def test_unknown_language_falls_back_to_english():
    assert i18n.init("zz") == "zz"
    assert i18n.translate("category.image") == "Image"


def test_formatting():
    i18n.init("en")
    text = i18n.translate("info.usage_time", hours=3, minutes=7)
    assert "3" in text and "7" in text


def test_shipped_languages():
    languages = i18n.available_languages()
    assert "en" in languages
    assert "tr" in languages


def test_language_detection_from_environment(monkeypatch):
    monkeypatch.setenv("NITOR_LANG", "tr_TR.UTF-8")
    assert i18n.init() == "tr"
