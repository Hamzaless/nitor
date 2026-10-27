# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.3.0] - 2026-10-27

### Added

- Update checks: Nitor asks the GitHub releases API once a day and shows a
  banner when a newer version exists. Manual check in the menu; can be turned
  off in Settings.
- Diagnostics: copy version, monitor, ddcutil and session details for bug
  reports.
- Settings: automatic update checks can be toggled.

## [1.2.1] - 2026-10-27

### Fixed

- The panel no longer hides while a dialog (settings, save preset, confirmation
  or about) is open. libadwaita dialogs are widgets rather than windows, so
  they are now tracked explicitly.

## [1.2.0] - 2026-10-27

### Added

- Presets: save the current monitor state and re-apply it from a dropdown.
- Settings dialog with language selection, auto-hide and slider delay.
- Full-window progress overlay with per-feature status while a preset runs.
- Locked-feature detection: Nitor warns when a monitor ignores a change
  because the current picture mode locks it.

### Changed

- All features are read in a single `ddcutil` call and the discovered I2C bus
  is reused, cutting start-up from roughly 15 s to about 2 s.
- Slider writes use a 150 ms debounce and a lower DDC sleep multiplier, so
  changes feel immediate.

## [1.1.0] - 2026-10-27

### Added

- Auto-hide: the panel now hides when you click somewhere outside of it.
  Disable it with `--no-auto-hide` or `NITOR_AUTO_HIDE=0`.

### Changed

- CI now runs on `actions/checkout@v5` and `actions/setup-python@v6`.

## [1.0.0] - 2026-10-27

### Added

- First public release.
- Feature discovery from the monitor capabilities string.
- Image controls: brightness, contrast, colour preset, RGB gain, gamma,
  saturation, scaling and display mode.
- Audio controls: speaker volume, mute and screen blank.
- Input and power: input source, power mode.
- OSD language selection.
- Read-only information: horizontal and vertical frequency, panel technology,
  usage time, application key, controller, firmware and VCP version.
- Confirmation dialogs for the three destructive VCP actions.
- English and Turkish interface.
- Cached values for instant startup, with a background refresh.
- Protection against monitors that reset brightness and contrast when the
  display mode changes.
- Single instance behaviour and an About dialog.
- Unit tests, lint configuration and GitHub Actions CI.

[1.3.0]: https://github.com/Hamzaless/nitor/releases/tag/v1.3.0
[1.2.1]: https://github.com/Hamzaless/nitor/releases/tag/v1.2.1
[1.2.0]: https://github.com/Hamzaless/nitor/releases/tag/v1.2.0
[1.1.0]: https://github.com/Hamzaless/nitor/releases/tag/v1.1.0
[1.0.0]: https://github.com/Hamzaless/nitor/releases/tag/v1.0.0
