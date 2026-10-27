"""Background daemon for scheduled and per-application presets.

The daemon is deliberately GTK-free: it can run under systemd, in a terminal
or as a ``nitor --daemon`` child without a display session.  Every tick it
reloads settings, applies at most one due scheduled preset and, when
``app_presets`` is configured, follows the focused Hyprland window class.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import signal
import subprocess
import threading
from datetime import datetime

from . import cli
from . import settings as settings_module

__all__ = ["active_window_class", "run_daemon"]

_LOGGER = logging.getLogger(__name__)
_STOP = threading.Event()


def _handle_signal(signum: int, _frame: object) -> None:
    _LOGGER.info("received signal %s, stopping", signum)
    _STOP.set()


def active_window_class() -> str | None:
    """Return the focused Hyprland window class, or None when unavailable."""
    hyprctl = shutil.which("hyprctl")
    if hyprctl is None:
        return None

    try:
        response = subprocess.run(
            [hyprctl, "activewindow", "-j"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _LOGGER.debug("hyprctl failed: %s", exc)
        return None

    if response.returncode != 0:
        return None

    try:
        data = json.loads(response.stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    window_class = data.get("class") or data.get("initialClass")
    if isinstance(window_class, str) and window_class.strip():
        return window_class.strip()
    return None


def _sync_app_preset(
    settings: settings_module.Settings,
    previous: str | None,
) -> str | None:
    """Apply the preset for the focused class and return the new class."""
    current = active_window_class()
    if current == previous:
        return previous
    if current is None:
        return None

    name = settings.app_presets.get(current)
    if name:
        if cli.apply_named_preset(settings, name):
            _LOGGER.info("applied preset %r for window class %r", name, current)
        else:
            _LOGGER.warning("could not apply preset %r for window class %r", name, current)
    return current


def run_daemon(interval: float = 30.0) -> int:
    """Run the preset daemon until SIGTERM or SIGINT, then return 0."""
    _STOP.clear()
    previous_handlers: dict[signal.Signals, object] = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous_handlers[signum] = signal.signal(signum, _handle_signal)
        except (ValueError, OSError) as exc:  # not the main thread
            _LOGGER.debug("cannot install handler for %s: %s", signum, exc)

    wait = max(1.0, interval)
    focused: str | None = None
    _LOGGER.info("daemon started (interval %.0fs)", wait)

    try:
        while not _STOP.is_set():
            try:
                settings = settings_module.Settings.load()
                cli.run_due_preset(settings, now=datetime.now())
                focused = _sync_app_preset(settings, focused) if settings.app_presets else None
            except Exception:
                _LOGGER.exception("daemon tick failed")
            _STOP.wait(wait)
    finally:
        for signum, handler in previous_handlers.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(signum, handler)
        _LOGGER.info("daemon stopped")

    return 0
