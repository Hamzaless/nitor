"""JSON import and export for user presets.

On disk a preset collection is ``{"format": 1, "presets": {...}}`` where every
preset maps a decimal VCP code to a byte value.  :func:`import_json` also
accepts the legacy layout, a bare ``{"name": {"16": 40}}`` mapping.
"""

from __future__ import annotations

import json
from typing import Any

from .presets import validate_name

__all__ = ["FORMAT_VERSION", "export_json", "import_json"]

#: Version written by :func:`export_json` and understood by :func:`import_json`.
FORMAT_VERSION = 1

_MAX_BYTE = 0xFF


def _parse_int(raw: Any) -> int:
    """Convert a JSON scalar to ``int``, accepting decimal or ``0x`` strings."""
    if isinstance(raw, bool):
        raise ValueError("boolean is not a number")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("empty string is not a number")
        try:
            return int(text, 0)
        except ValueError:
            return int(text, 10)
    raise ValueError(f"expected a number, got {type(raw).__name__}")


def _clean_values(name: str, values: Any) -> dict[str, int]:
    if not isinstance(values, dict):
        raise ValueError(f"preset {name!r}: expected a mapping of VCP code to value")

    cleaned: dict[int, int] = {}
    for raw_code, raw_value in values.items():
        try:
            code = _parse_int(raw_code)
        except ValueError:
            raise ValueError(f"preset {name!r}: invalid VCP code {raw_code!r}") from None
        if not 0 <= code <= _MAX_BYTE:
            raise ValueError(f"preset {name!r}: VCP code {raw_code!r} must be 0-255")

        try:
            value = _parse_int(raw_value)
        except ValueError:
            raise ValueError(
                f"preset {name!r}: invalid value {raw_value!r} for code {code}"
            ) from None
        if not 0 <= value <= _MAX_BYTE:
            raise ValueError(
                f"preset {name!r}: value {raw_value!r} for code {code} must be 0-255"
            )
        cleaned[code] = value

    return {str(code): cleaned[code] for code in sorted(cleaned)}


def _clean_presets(presets: Any) -> dict[str, dict[str, int]]:
    if not isinstance(presets, dict):
        raise ValueError("presets must be a mapping of preset name to values")

    cleaned: dict[str, dict[str, int]] = {}
    for raw_name, values in presets.items():
        try:
            name = validate_name(raw_name)
        except ValueError as exc:
            raise ValueError(f"invalid preset name {raw_name!r}: {exc}") from None
        if name in cleaned:
            raise ValueError(f"duplicate preset name {name!r} after trimming")
        cleaned[name] = _clean_values(name, values)

    return {name: cleaned[name] for name in sorted(cleaned)}


def export_json(presets: dict[str, dict[str, int]]) -> str:
    """Serialise ``presets`` as a versioned JSON document with sorted keys."""
    payload = {"format": FORMAT_VERSION, "presets": _clean_presets(presets)}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _extract_presets(data: dict[str, Any]) -> Any:
    """Return the preset mapping from a versioned or legacy document."""
    versioned = "presets" in data and ("format" in data or list(data) == ["presets"])
    if not versioned:
        return data

    version = data.get("format", FORMAT_VERSION)
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise ValueError(
            f"unsupported preset format {version!r}; expected {FORMAT_VERSION}"
        )
    return data["presets"]


def import_json(text: str) -> dict[str, dict[str, int]]:
    """Parse ``text`` into a validated preset mapping.

    Accepts the versioned format written by :func:`export_json` as well as the
    legacy bare mapping.  Every problem raises :class:`ValueError` with a
    message naming the offending preset or field.
    """
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid preset JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("invalid preset JSON: expected an object at the top level")

    presets = _extract_presets(data)
    if not isinstance(presets, dict):
        raise ValueError('invalid preset JSON: "presets" must be an object')
    return _clean_presets(presets)
