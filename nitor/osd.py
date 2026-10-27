"""On-screen notifications through Hyprland's ``hyprctl``.

Nitor uses the compositor's own notification overlay instead of pulling in a
notification daemon.  Everything here degrades quietly: on a non-Hyprland
session :func:`available` is False and :func:`show` simply does nothing.
"""

from __future__ import annotations

import shutil
import subprocess

__all__ = ["available", "build_command", "show"]

#: hyprctl returns immediately, but keep a ceiling in case it wedges.
TIMEOUT = 5.0

#: Hyprland notification colour, matching the app's light accent.
COLOUR = "rgb(e7e1e2)"


def available() -> bool:
    """Return True when ``hyprctl`` is on PATH."""
    return shutil.which("hyprctl") is not None


def build_command(text: str, duration_ms: int = 1500) -> list[str]:
    """Return the argument vector used by :func:`show`.

    Double quotes are escaped because hyprctl's notification text ends up in
    the compositor's command parser.
    """
    escaped = str(text).replace('"', '\\"')
    return [
        "hyprctl",
        "notify",
        "-1",
        str(int(duration_ms)),
        COLOUR,
        escaped,
    ]


def show(text: str, duration_ms: int = 1500) -> bool:
    """Show ``text`` for ``duration_ms``; return True on success.

    Never raises: missing binaries, compositor errors and subprocess failures
    all result in False.
    """
    if not available():
        return False
    try:
        command = build_command(text, duration_ms)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
