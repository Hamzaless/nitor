# Contributing to Nitor

Thanks for taking the time to help. This project is small on purpose: a thin
GTK 4 front end around `ddcutil`. Keeping it that way is a feature.

## Development setup

```bash
git clone https://github.com/Hamzaless/nitor.git
cd nitor

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

System requirements (Fedora):

```bash
sudo dnf install python3-gobject gtk4 libadwaita ddcutil
```

Run from the checkout:

```bash
python3 -m nitor --debug
```

## Tests and lint

```bash
pytest -q
ruff check .
```

Tests never touch a real monitor: `ddcutil` calls are mocked, so they run on
any machine and in CI.

## Adding a language

1. Copy `nitor/translations/en.json` to `nitor/translations/<code>.json`.
2. Translate the values, keep the keys unchanged.
3. Add the language to the README table.
4. Run `pytest -q` — a test checks that every shipped file is valid JSON.

Missing keys fall back to English, so a partial translation is fine.

## Adding a VCP feature

1. Add a `FeatureMeta` entry to `FEATURES` in `nitor/ddc.py`.
2. Add its key to `DISPLAY_ORDER`.
3. Add the label to both translation files.
4. Add or update a row in the README feature table.

Please **do not** add undocumented manufacturer-specific codes (for example
`0xE2`) to the UI. Writing unknown VCP values can reset picture settings or
leave a monitor in a broken state. Nitor intentionally ignores them.

## Commit style

Short imperative subject lines, for example:

```
Add Turkish translation
Fix brightness reset when changing display mode
```

## Reporting bugs

Include your distribution, desktop, monitor model, GPU driver and the output of:

```bash
ddcutil detect
ddcutil capabilities
nitor --version
```
