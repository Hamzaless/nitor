"""Command line entry point for Nitor."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .i18n import init as init_i18n


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    init_i18n(args.lang)

    # Import after logging and translations are configured.
    from .ui import NitorApplication

    application = NitorApplication()
    return application.run([sys.argv[0]])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
