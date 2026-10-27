"""Persistent user settings stored in ``$XDG_CONFIG_HOME/nitor/settings.json``."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["Settings", "config_dir", "config_path"]

_TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")


def _clean_schedule(raw: object) -> list[dict[str, object]]:
    """Keep only well-formed schedule entries."""
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        time = item.get("time")
        preset = item.get("preset")
        if not isinstance(time, str) or not _TIME_RE.match(time.strip()):
            continue
        if not isinstance(preset, str) or not preset.strip():
            continue
        entry: dict[str, object] = {"time": time.strip(), "preset": preset.strip()[:60]}
        days = item.get("days")
        if isinstance(days, list):
            cleaned_days = sorted(
                {
                    day
                    for day in days
                    if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6
                }
            )
            if cleaned_days:
                entry["days"] = cleaned_days
        entries.append(entry)
    return entries


def _clean_app_presets(raw: object) -> dict[str, str]:
    """Keep ``{window_class: preset_name}`` entries with string values."""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for window_class, preset in raw.items():
        if not isinstance(window_class, str) or not window_class.strip():
            continue
        if not isinstance(preset, str) or not preset.strip():
            continue
        cleaned[window_class.strip()[:120]] = preset.strip()[:60]
    return cleaned


def _clean_last_run(raw: object) -> dict[str, str]:
    """Keep ``{preset_name: ISO date}`` entries."""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name:
            continue
        if isinstance(value, str) and value:
            cleaned[name] = value
    return cleaned


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "nitor"


def config_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    language: str = "system"
    auto_hide: bool = True
    check_updates: bool = True
    debounce_ms: int = 150
    presets: dict[str, dict[str, int]] = field(default_factory=dict)
    schedule: list[dict[str, object]] = field(default_factory=list)
    app_presets: dict[str, str] = field(default_factory=dict)
    last_run: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Settings:
        try:
            raw = json.loads(config_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()

        settings = cls()
        language = raw.get("language")
        if isinstance(language, str) and language:
            settings.language = language
        auto_hide = raw.get("auto_hide")
        if isinstance(auto_hide, bool):
            settings.auto_hide = auto_hide
        check_updates = raw.get("check_updates")
        if isinstance(check_updates, bool):
            settings.check_updates = check_updates
        debounce = raw.get("debounce_ms")
        if isinstance(debounce, int):
            settings.debounce_ms = max(50, min(2000, debounce))

        presets = raw.get("presets")
        if isinstance(presets, dict):
            cleaned: dict[str, dict[str, int]] = {}
            for name, values in presets.items():
                if not isinstance(name, str) or not isinstance(values, dict):
                    continue
                entries: dict[str, int] = {}
                for code, value in values.items():
                    try:
                        entries[str(int(code))] = int(value)
                    except (TypeError, ValueError):
                        continue
                if entries:
                    cleaned[name[:60]] = entries
            settings.presets = cleaned

        settings.schedule = _clean_schedule(raw.get("schedule"))
        settings.app_presets = _clean_app_presets(raw.get("app_presets"))
        settings.last_run = _clean_last_run(raw.get("last_run"))
        return settings

    def save(self) -> None:
        path = config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
