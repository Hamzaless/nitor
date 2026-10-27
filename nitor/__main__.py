"""Allow ``python -m nitor``."""

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
