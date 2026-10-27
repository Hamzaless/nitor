#!/bin/sh
# Flatpak launcher: make the bundled Python package importable.
export PYTHONPATH=/app/share/nitor${PYTHONPATH:+:$PYTHONPATH}
exec python3 -m nitor "$@"
