"""GitHub release based update checks.

The check is deliberately tiny: one HTTPS request to the public releases API,
no download, no automatic install.  Nitor only tells the user that a newer
release exists and opens the release page on request.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from . import __version__

__all__ = ["Release", "is_newer", "latest_release", "parse_version"]

API_URL = "https://api.github.com/repos/Hamzaless/nitor/releases/latest"
USER_AGENT = f"Nitor/{__version__}"
TIMEOUT = 8.0


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    notes: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """Turn ``v1.2.3-beta4`` into ``(1, 2, 3, 4)`` for comparison."""
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(number) for number in numbers) or (0,)


def is_newer(latest: str, current: str) -> bool:
    left = parse_version(latest)
    right = parse_version(current)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


def latest_release() -> Release | None:
    """Return the newest published release, or None if the API has no data."""
    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.load(response)

    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").lstrip("vV")
    url = str(payload.get("html_url") or "")
    if not tag or not url:
        return None
    return Release(version=tag, url=url, notes=str(payload.get("body") or ""))
