"""Tests for the tray menu description and graceful startup."""

from __future__ import annotations

import sys

from nitor import tray


def test_build_menu_with_presets():
    menu = tray.build_menu(["Work", "Night"], "Night")

    assert [item.get("id") for item in menu] == [
        "preset:Work",
        "preset:Night",
        None,
        "open",
        "quit",
    ]
    assert menu[0]["toggle-state"] == 0
    assert menu[1]["toggle-state"] == 1
    assert menu[2] == {"type": "separator"}
    assert menu[3]["id"] == "open"
    assert menu[4]["id"] == "quit"


def test_build_menu_without_presets_has_no_separator():
    menu = tray.build_menu([], None)

    assert [item.get("id") for item in menu] == ["open", "quit"]
    assert {"type": "separator"} not in menu


def test_build_menu_without_active_preset_checks_nothing():
    menu = tray.build_menu(["Work"], None)
    assert [item.get("toggle-state") for item in menu if "toggle-state" in item] == [0]


def test_start_without_pygobject_returns_false(monkeypatch):
    monkeypatch.setitem(sys.modules, "gi", None)

    icon = tray.TrayIcon()
    assert icon.start() is False
    icon.stop()


def test_stop_is_safe_without_start():
    tray.TrayIcon().stop()


def test_activation_before_layout_still_resolves_items():
    seen: list[str] = []
    icon = tray.TrayIcon(
        presets=["Work", "Night"],
        on_preset=seen.append,
        on_open=lambda: seen.append("open"),
    )

    icon._activate(2)  # Night, no GetLayout call before the click
    icon._activate(4)  # Open Nitor

    assert seen == ["Night", "open"]
