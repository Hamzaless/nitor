"""Persistent user settings stored in ``$XDG_CONFIG_HOME/nitor/settings.json``."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["Settings", "config_dir", "config_path"]


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
