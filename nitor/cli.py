"""Non-interactive command line interface for Nitor.

``try_run`` is called by :func:`nitor.app.main` before the GTK application is
constructed.  It returns ``None`` for every invocation that does not contain a
CLI command, so ordinary GUI starts (including ``--version``, ``--lang``,
``--debug`` and ``--no-auto-hide``) keep working unchanged.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import datetime

from . import ddc, presets, schedule
from . import settings as settings_module

__all__ = [
    "apply_named_preset",
    "parse_code",
    "run_due_preset",
    "try_run",
]

_COMMANDS = {
    "--get",
    "--set",
    "--list-presets",
    "--apply-preset",
    "--schedule-apply",
    "--daemon",
    "--menu",
}

_MENU_PROMPT = "Nitor preset"


def _error(message: str) -> None:
    print(f"nitor: {message}", file=sys.stderr)


def parse_code(text: str) -> int:
    """Parse a VCP code written in decimal (``16``) or hexadecimal (``0x10``)."""
    stripped = text.strip()
    base = 16 if stripped.lower().startswith("0x") else 10
    code = int(stripped, base)
    if not 0 <= code <= 0xFF:
        raise ValueError(f"VCP code out of range: {text!r}")
    return code


def _parse_value(text: str) -> int:
    stripped = text.strip()
    base = 16 if stripped.lower().startswith("0x") else 10
    value = int(stripped, base)
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"value out of range: {text!r}")
    return value


def _parse_assignment(text: str) -> tuple[int, int]:
    code_text, separator, value_text = text.partition("=")
    if not separator:
        raise ValueError(f"expected CODE=VALUE, got {text!r}")
    return parse_code(code_text), _parse_value(value_text)


def apply_named_preset(settings: settings_module.Settings, name: str) -> bool:
    """Apply a stored preset, returning True on success.

    Codes are ordered through :func:`nitor.presets.normalized`; unknown codes
    are dropped.  A lower ``sleep-multiplier`` makes bulk writes faster.
    """
    raw = settings.presets.get(name)
    if not isinstance(raw, dict):
        return False

    values: dict[int, int] = {}
    for code, value in raw.items():
        try:
            values[int(code)] = int(value)
        except (TypeError, ValueError):
            continue

    ordered = presets.normalized(values)
    if not ordered:
        return False

    success = True
    for code, value in ordered:
        if not ddc.set_value(code, value, sleep_multiplier="0.1"):
            success = False
    return success


def run_due_preset(
    settings: settings_module.Settings,
    now: datetime | None = None,
) -> int:
    """Apply the first due scheduled preset and record the run.

    Returns 0 when nothing was due or the preset was applied successfully, and
    1 when a due preset could not be applied.  A failed run is not recorded,
    so it is retried on the next invocation.
    """
    moment = now or datetime.now()
    entries = settings.schedule if isinstance(settings.schedule, list) else []
    last_run = settings.last_run if isinstance(settings.last_run, dict) else {}
    name = schedule.due_preset(moment, entries, last_run)
    if name is None:
        return 0

    if not apply_named_preset(settings, name):
        _error(f"could not apply scheduled preset {name!r}")
        return 1

    settings.last_run[name] = moment.date().isoformat()
    settings.save()
    return 0


def _run_get(code_text: str) -> int:
    try:
        code = parse_code(code_text)
    except ValueError as exc:
        _error(str(exc))
        return 2

    try:
        current, _maximum = ddc.get(code)
    except ddc.DdcError as exc:
        _error(str(exc))
        return 1

    if current is None:
        _error(f"VCP 0x{code:02X} returned no value")
        return 1

    print(current)
    return 0


def _run_set(assignments: list[str]) -> int:
    pairs: list[tuple[int, int]] = []
    for text in assignments:
        try:
            pairs.append(_parse_assignment(text))
        except ValueError as exc:
            _error(str(exc))
            return 2

    for code, value in pairs:
        try:
            success = ddc.set_value(code, value)
        except ddc.DdcError as exc:
            _error(str(exc))
            return 1
        if not success:
            _error(f"could not set VCP 0x{code:02X} to {value}")
            return 1
    return 0


def _run_apply_preset(settings: settings_module.Settings, name: str) -> int:
    if name not in settings.presets:
        _error(f"unknown preset: {name}")
        return 1
    if not apply_named_preset(settings, name):
        _error(f"could not apply preset: {name}")
        return 1
    return 0


def _run_menu(settings: settings_module.Settings) -> int:
    wofi = shutil.which("wofi")
    rofi = shutil.which("rofi")
    if wofi:
        command = [wofi, "--dmenu", "--prompt", _MENU_PROMPT]
    elif rofi:
        command = [rofi, "-dmenu", "-p", _MENU_PROMPT]
    else:
        _error("no menu program found; install wofi or rofi")
        return 1

    names = sorted(settings.presets)
    if not names:
        _error("no presets saved")
        return 1

    try:
        response = subprocess.run(
            command,
            input="\n".join(names) + "\n",
            capture_output=True,
            text=True,
            timeout=120.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _error(str(exc))
        return 1

    choice = response.stdout.strip()
    if response.returncode != 0 or not choice or choice not in settings.presets:
        return 1
    return _run_apply_preset(settings, choice)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nitor", add_help=False)
    parser.add_argument("--get", metavar="CODE", help="print the current value of a VCP code")
    parser.add_argument(
        "--set",
        metavar="CODE=VALUE",
        action="append",
        default=[],
        help="write a VCP value; may be repeated",
    )
    parser.add_argument("--list-presets", action="store_true", help="print saved preset names")
    parser.add_argument("--apply-preset", metavar="NAME", help="apply a saved preset")
    parser.add_argument(
        "--schedule-apply",
        action="store_true",
        help="apply the first due scheduled preset",
    )
    parser.add_argument("--daemon", action="store_true", help="run the scheduling daemon")
    parser.add_argument("--menu", action="store_true", help="pick a preset with wofi or rofi")
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    return parser


def _has_command(argv: list[str]) -> bool:
    return any(argument.split("=", 1)[0] in _COMMANDS for argument in argv)


def try_run(argv: list[str]) -> int | None:
    """Run a CLI command from ``argv``, or return None for a GUI start."""
    if not _has_command(argv):
        return None

    parser = _build_parser()
    try:
        args, _unknown = parser.parse_known_args(argv)
    except SystemExit as exc:  # argparse already printed the error
        return exc.code if isinstance(exc.code, int) else 1

    actions = [
        args.get is not None,
        bool(args.set),
        args.list_presets,
        args.apply_preset is not None,
        args.schedule_apply,
        args.daemon,
        args.menu,
    ]
    if sum(actions) > 1:
        _error("choose one action at a time")
        return 2
    if not any(actions):
        return None

    if args.get is not None:
        return _run_get(args.get)
    if args.set:
        return _run_set(args.set)
    if args.daemon:
        logging.basicConfig(
            level=logging.DEBUG if args.debug else logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
        )
        from .daemon import run_daemon

        return run_daemon()

    settings = settings_module.Settings.load()

    if args.list_presets:
        for name in sorted(settings.presets):
            print(name)
        return 0
    if args.apply_preset is not None:
        return _run_apply_preset(settings, args.apply_preset)
    if args.schedule_apply:
        return run_due_preset(settings)
    if args.menu:
        return _run_menu(settings)

    return None
