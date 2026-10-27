"""Search helpers for the feature list.

Search is intentionally dumb and predictable: a case-insensitive substring on
the translated feature name, plus exact matches on the VCP code written in
hex (``0x10`` or ``10``) or decimal (``16``).  No fuzzy matching, no scoring.
"""

from __future__ import annotations

__all__ = ["normalize_query", "filter_codes"]

_HEX_DIGITS = frozenset("0123456789abcdef")


def normalize_query(q: str) -> str:
    """Trim and casefold a user query."""
    return (q or "").strip().casefold()


def filter_codes(query: str, names: dict[int, str]) -> list[int]:
    """Return the codes whose name or numeric form matches ``query``.

    Results are sorted ascending.  An empty query returns every known code.
    """
    needle = normalize_query(query)
    codes = sorted(names)
    if not needle:
        return codes

    decimal: int | None = None
    hexadecimal: int | None = None
    if needle.isdigit():
        decimal = int(needle)
    if needle.startswith("0x"):
        try:
            hexadecimal = int(needle[2:], 16)
        except ValueError:
            hexadecimal = None
    elif needle and all(character in _HEX_DIGITS for character in needle):
        try:
            hexadecimal = int(needle, 16)
        except ValueError:
            hexadecimal = None

    matches: list[int] = []
    for code in codes:
        name = str(names.get(code) or "").casefold()
        if needle in name or code in (decimal, hexadecimal):
            matches.append(code)
    return matches
