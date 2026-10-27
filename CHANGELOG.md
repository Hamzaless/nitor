# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

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

[1.0.0]: https://github.com/Hamzaless/nitor/releases/tag/v1.0.0
