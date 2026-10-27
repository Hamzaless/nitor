"""Command line entry point for Nitor."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import __version__
from .i18n import init as init_i18n
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nitor",
        description="Monitor control panel for Linux, built on DDC/CI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"nitor {__version__}",
    )
    parser.add_argument(
        "--lang",
        metavar="CODE",
        help="interface language, for example en or tr",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable verbose logging",
    )
    parser.add_argument(
        "--no-auto-hide",
        action="store_true",
        help="keep the window open when it loses focus",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.load()
    language = args.lang or (None if settings.language == "system" else settings.language)
    init_i18n(language)

    auto_hide = not args.no_auto_hide
    if os.environ.get("NITOR_AUTO_HIDE", "").strip().lower() in {"0", "false", "no", "off"}:
        auto_hide = False

    # Import after logging and translations are configured.
    from .ui import NitorApplication

    settings.auto_hide = auto_hide
    application = NitorApplication(settings)
    return application.run([sys.argv[0]])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
