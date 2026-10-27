"""Helpers for applying and managing user presets.

A preset is a small mapping of VCP code to value.  Values are written in a
fixed order: colour mode first, brightness and contrast last, because many
monitors reset picture settings when the colour mode changes.
"""

from __future__ import annotations

__all__ = [
    "APPLY_ORDER",
    "CAPTURE_CODES",
    "MAX_NAME_LENGTH",
    "delete_preset",
    "diff_preset",
    "duplicate_preset",
    "normalized",
    "rename_preset",
    "sort_presets",
    "validate_name",
]

#: Values captured when the user saves the current state as a preset.
CAPTURE_CODES: tuple[int, ...] = (0x10, 0x12, 0x14, 0x72, 0x8A, 0x62)

#: Write order when applying a preset.
APPLY_ORDER: tuple[int, ...] = (0xDC, 0x14, 0x8A, 0x72, 0x12, 0x10, 0x62)

#: Longest allowed preset name, in characters.
MAX_NAME_LENGTH = 60


def normalized(values: dict[int, int]) -> list[tuple[int, int]]:
    """Return ``values`` as a list ordered by :data:`APPLY_ORDER`."""
    return [(code, values[code]) for code in APPLY_ORDER if code in values]


def validate_name(name: str) -> str:
    """Return ``name`` stripped, raising :class:`ValueError` when unusable."""
    if not isinstance(name, str):
        raise ValueError("preset name must be a string")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("preset name must not be empty")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError(f"preset name must be at most {MAX_NAME_LENGTH} characters")
    return cleaned


def _copy_presets(presets: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return {name: dict(values) for name, values in presets.items()}


def rename_preset(
    presets: dict[str, dict[str, int]], old: str, new: str
) -> dict[str, dict[str, int]]:
    """Return a copy of ``presets`` with ``old`` renamed to ``new``.

    The position of the renamed preset is preserved.  ``KeyError`` is raised
    when ``old`` is unknown, ``ValueError`` when ``new`` is invalid or taken.
    """
    if old not in presets:
        raise KeyError(old)
    target = validate_name(new)
    if target == old:
        return _copy_presets(presets)
    if target in presets:
        raise ValueError(f"a preset named {target!r} already exists")

    renamed: dict[str, dict[str, int]] = {}
    for name, values in presets.items():
        if name == old:
            renamed[target] = dict(values)
        else:
            renamed[name] = dict(values)
    return renamed


def _with_suffix(base: str, suffix: str) -> str:
    """Append ``suffix`` to ``base`` while staying within the name limit."""
    room = MAX_NAME_LENGTH - len(suffix)
    return base[:room].rstrip() + suffix


def _unique_name(presets: dict[str, dict[str, int]], base: str) -> str:
    candidate = base[:MAX_NAME_LENGTH].rstrip()
    counter = 2
    while candidate in presets:
        candidate = _with_suffix(base, f" {counter}")
        counter += 1
    return candidate


def duplicate_preset(
    presets: dict[str, dict[str, int]], name: str, new_name: str | None = None
) -> dict[str, dict[str, int]]:
    """Return a copy of ``presets`` with ``name`` duplicated.

    The copy is named ``"<name> copy"`` unless ``new_name`` is given; a numeric
    suffix keeps the default name unique.  ``KeyError`` is raised when ``name``
    is unknown and ``ValueError`` when ``new_name`` is invalid or taken.
    """
    if name not in presets:
        raise KeyError(name)

    if new_name is None:
        base = _with_suffix(name, " copy")
        target = _unique_name(presets, base)
    else:
        target = validate_name(new_name)
        if target in presets:
            raise ValueError(f"a preset named {target!r} already exists")

    duplicated = _copy_presets(presets)
    duplicated[target] = dict(presets[name])
    return duplicated


def delete_preset(
    presets: dict[str, dict[str, int]], name: str
) -> dict[str, dict[str, int]]:
    """Return a copy of ``presets`` without ``name``, raising ``KeyError``."""
    if name not in presets:
        raise KeyError(name)
    return {key: dict(values) for key, values in presets.items() if key != name}


def sort_presets(
    presets: dict[str, dict[str, int]], names: list[str]
) -> dict[str, dict[str, int]]:
    """Return ``presets`` reordered by ``names``.

    Unknown entries in ``names`` are ignored, duplicates are skipped and any
    preset missing from ``names`` keeps its relative order at the end.
    """
    ordered: dict[str, dict[str, int]] = {}
    for name in names:
        if name in presets and name not in ordered:
            ordered[name] = dict(presets[name])
    for name, values in presets.items():
        if name not in ordered:
            ordered[name] = dict(values)
    return ordered


def diff_preset(
    current: dict[int, int], target: dict[int, int]
) -> dict[int, tuple[int | None, int]]:
    """Return the codes of ``target`` that differ from ``current``.

    Each entry maps a code to ``(current_value, target_value)``; the current
    value is ``None`` when the code is not present in ``current``.
    """
    changes: dict[int, tuple[int | None, int]] = {}
    for code in sorted(target):
        value = target[code]
        before = current.get(code)
        if before != value:
            changes[code] = (before, value)
    return changes
