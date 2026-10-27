"""Tests for the Hyprland OSD helper (no compositor required)."""

from __future__ import annotations

import subprocess

import pytest

from nitor import osd


def test_build_command_shape():
    assert osd.build_command("Brightness 50%", 2000) == [
        "hyprctl",
        "notify",
        "-1",
        "2000",
        "rgb(e7e1e2)",
        "Brightness 50%",
    ]


def test_build_command_default_duration():
    assert osd.build_command("hi")[3] == "1500"


def test_build_command_escapes_double_quotes():
    command = osd.build_command('Set "Cinema" mode', 900)
    assert command[-1] == 'Set \\"Cinema\\" mode'
    assert command[3] == "900"


def test_show_is_false_when_hyprctl_is_missing(monkeypatch):
    monkeypatch.setattr(osd.shutil, "which", lambda _name: None)

    def explode(*_args, **_kwargs):  # pragma: no cover - must not be called
        raise AssertionError("subprocess must not run without hyprctl")

    monkeypatch.setattr(osd.subprocess, "run", explode)
    assert osd.available() is False
    assert osd.show("unavailable") is False


def test_show_runs_hyprctl_without_a_shell(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert kwargs.get("shell") is None or kwargs["shell"] is False
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(osd.shutil, "which", lambda _name: "/usr/bin/hyprctl")
    monkeypatch.setattr(osd.subprocess, "run", fake_run)

    assert osd.show("Volume 40", 900) is True
    assert calls == [
        [
            "hyprctl",
            "notify",
            "-1",
            "900",
            "rgb(e7e1e2)",
            "Volume 40",
        ]
    ]


def test_show_returns_false_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(osd.shutil, "which", lambda _name: "/usr/bin/hyprctl")
    monkeypatch.setattr(
        osd.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "err"),
    )
    assert osd.show("nope") is False


@pytest.mark.parametrize("error", [OSError("missing"), subprocess.TimeoutExpired("hyprctl", 5)])
def test_show_never_raises(monkeypatch, error):
    monkeypatch.setattr(osd.shutil, "which", lambda _name: "/usr/bin/hyprctl")

    def boom(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(osd.subprocess, "run", boom)
    assert osd.show("boom") is False
