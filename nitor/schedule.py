"""Scheduled preset evaluation.

This module is pure: it does not read the clock, the file system or GTK.  The
caller passes ``now``, which makes the decision deterministic and easy to test.

A schedule entry looks like::

    {"time": "20:00", "preset": "Gece", "days": [0, 1, 2, 3, 4]}

``days`` is optional and uses Python's weekday numbering (Monday is 0, Sunday
is 6).  A preset is applied at most once per day: once it has run,
``last_run[preset]`` contains that day's ISO date and the entry is skipped
until the date changes.
"""

from __future__ import annotations

import re
from datetime import datetime

__all__ = ["due_preset", "parse_time"]

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_time(value: object) -> int | None:
    """Return ``HH:MM`` as minutes since midnight, or None when invalid."""
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _last_run_date(value: object) -> str | None:
    """Accept either an ISO date string or ``{"date": "..."}``."""
    if isinstance(value, dict):
        value = value.get("date")
    return value if isinstance(value, str) else None


def due_preset(
    now: datetime,
    entries: list[dict],
    last_run: dict[str, str],
) -> str | None:
    """Return the name of the first preset due at ``now``, or None.

    An entry is due when the current time is at or past its ``time``, today is
    included in ``days`` (when given), and the preset has not already run
    today according to ``last_run``.  Malformed entries are ignored instead of
    raising, so a bad settings file cannot crash the daemon.

    Entries are evaluated in order, which makes the result deterministic when
    several presets share the same time: the first one wins.
    """
    today = now.date().isoformat()
    minutes = now.hour * 60 + now.minute

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        scheduled = parse_time(entry.get("time"))
        preset = entry.get("preset")
        if scheduled is None or not isinstance(preset, str) or not preset.strip():
            continue
        if minutes < scheduled:
            continue

        days = entry.get("days")
        if days is not None:
            if not isinstance(days, (list, tuple)):
                continue
            if days:
                allowed = {
                    day
                    for day in days
                    if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6
                }
                if not allowed or now.weekday() not in allowed:
                    continue

        previous = _last_run_date(last_run.get(preset)) if last_run else None
        if previous == today:
            continue

        return preset

    return None
