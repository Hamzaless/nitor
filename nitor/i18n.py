"""Minimal JSON based translation layer.

Translations live in ``nitor/translations/<lang>.json``.  English is the
source language and also the fallback for missing keys, so a partially
translated language file never breaks the interface.

Language selection order:

1. ``NITOR_LANG`` environment variable (``tr``, ``tr_TR``, ``tr_TR.UTF-8``)
2. ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG``
3. the C library locale
4. English
"""

from __future__ import annotations

import contextlib
import json
import locale
import os
from pathlib import Path
from typing import Any

__all__ = ["get_language", "init", "available_languages", "translate"]

_TRANSLATIONS: dict[str, str] = {}
_FALLBACK: dict[str, str] = {}
_LANGUAGE = "en"
_TRANSLATION_DIR = Path(__file__).with_name("translations")


def _read(language: str) -> dict[str, str] | None:
    path = _TRANSLATION_DIR / f"{language}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if isinstance(v, str)}


def available_languages() -> list[str]:
    """Return the language codes shipped with this build."""
    if not _TRANSLATION_DIR.is_dir():
        return ["en"]
    return sorted(p.stem for p in _TRANSLATION_DIR.glob("*.json"))


def _detect_language() -> str:
    override = os.environ.get("NITOR_LANG")
    candidates = [override] if override else []
    candidates += [
        os.environ.get("LC_ALL"),
        os.environ.get("LC_MESSAGES"),
        os.environ.get("LANG"),
    ]
    with contextlib.suppress(ValueError, TypeError):
        candidates.append(locale.getlocale()[0])

    available = set(available_languages())
    for value in candidates:
        if not value:
            continue
        code = value.split(".", 1)[0].split("_", 1)[0].strip().lower()
        if code in {"", "c", "posix"}:
            continue
        if code in available:
            return code
    return "en"


def init(language: str | None = None) -> str:
    """Load translations and return the language that was selected."""
    global _TRANSLATIONS, _FALLBACK, _LANGUAGE

    _FALLBACK = _read("en") or {}
    _LANGUAGE = (language or _detect_language()).split("_", 1)[0].lower()

    if _LANGUAGE == "en":
        _TRANSLATIONS = dict(_FALLBACK)
    else:
        _TRANSLATIONS = dict(_FALLBACK)
        _TRANSLATIONS.update(_read(_LANGUAGE) or {})
    return _LANGUAGE


def get_language() -> str:
    return _LANGUAGE


def translate(key: str, **values: Any) -> str:
    """Translate *key* and format it with *values*.

    Unknown keys are returned unchanged so the UI degrades gracefully.
    """
    text = _TRANSLATIONS.get(key) or _FALLBACK.get(key) or key
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


# Short alias used throughout the UI code.
_ = translate
