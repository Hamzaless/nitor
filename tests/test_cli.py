"""Tests for the non-interactive CLI; no monitor or GTK required."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from nitor import cli, ddc
from nitor.settings import Settings


def _save_settings(tmp_path, monkeypatch, **fields) -> Settings:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = Settings(**fields)
    settings.save()
    return settings


def test_gui_only_arguments_return_none():
    assert cli.try_run([]) is None
    assert cli.try_run(["--version"]) is None
    assert cli.try_run(["--lang", "tr"]) is None
    assert cli.try_run(["--debug"]) is None
    assert cli.try_run(["--no-auto-hide"]) is None


def test_parse_code_accepts_decimal_and_hex():
    assert cli.parse_code("16") == 16
    assert cli.parse_code("0x10") == 16
    assert cli.parse_code(" 0XFF ") == 0xFF
    with pytest.raises(ValueError):
        cli.parse_code("0x100")


def test_get_prints_value(monkeypatch, capsys):
    seen: list[int] = []

    def fake_get(code, **_kwargs):
        seen.append(code)
        return 40, 100

    monkeypatch.setattr(ddc, "get", fake_get)
    assert cli.try_run(["--get", "0x10"]) == 0
    assert seen == [0x10]
    assert capsys.readouterr().out == "40\n"


def test_get_decimal_code(monkeypatch, capsys):
    monkeypatch.setattr(ddc, "get", lambda code, **_kwargs: (12, None))
    assert cli.try_run(["--get", "16"]) == 0
    assert capsys.readouterr().out == "12\n"


def test_get_failure_exits_one(monkeypatch, capsys):
    monkeypatch.setattr(ddc, "get", lambda code, **_kwargs: (None, None))
    assert cli.try_run(["--get", "0x10"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "0x10" in captured.err


def test_get_rejects_bad_code(capsys):
    assert cli.try_run(["--get", "xyz"]) == 2
    assert "nitor:" in capsys.readouterr().err


def test_set_is_repeatable(monkeypatch):
    calls: list[tuple[int, int]] = []

    def fake_set(code, value, **_kwargs):
        calls.append((code, value))
        return True

    monkeypatch.setattr(ddc, "set_value", fake_set)
    assert cli.try_run(["--set", "0x10=45", "--set", "18=60", "--set=0x14=5"]) == 0
    assert calls == [(0x10, 45), (18, 60), (0x14, 5)]


def test_set_failure_exits_one(monkeypatch, capsys):
    monkeypatch.setattr(ddc, "set_value", lambda code, value, **_kwargs: False)
    assert cli.try_run(["--set", "0x10=45"]) == 1
    assert "0x10" in capsys.readouterr().err


def test_set_rejects_malformed_assignment_before_writing(monkeypatch, capsys):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(ddc, "set_value", lambda *args, **kwargs: calls.append(args) or True)
    assert cli.try_run(["--set", "0x10=45", "--set", "broken"]) == 2
    assert calls == []
    assert "CODE=VALUE" in capsys.readouterr().err


def test_list_presets_is_sorted(tmp_path, monkeypatch, capsys):
    _save_settings(
        tmp_path,
        monkeypatch,
        presets={"Gunduz": {"16": 80}, "Gece": {"16": 20}},
    )
    assert cli.try_run(["--list-presets"]) == 0
    assert capsys.readouterr().out.splitlines() == ["Gece", "Gunduz"]


def test_apply_preset_uses_normalized_order(tmp_path, monkeypatch):
    _save_settings(
        tmp_path,
        monkeypatch,
        presets={"Gece": {"16": 20, "18": 80, "99": 5}},
    )
    calls: list[tuple[int, int, dict]] = []

    def fake_set(code, value, **kwargs):
        calls.append((code, value, kwargs))
        return True

    monkeypatch.setattr(ddc, "set_value", fake_set)
    assert cli.try_run(["--apply-preset", "Gece"]) == 0
    assert calls == [
        (0x12, 80, {"sleep_multiplier": "0.1"}),
        (0x10, 20, {"sleep_multiplier": "0.1"}),
    ]


def test_apply_unknown_preset_exits_one(tmp_path, monkeypatch, capsys):
    _save_settings(tmp_path, monkeypatch, presets={"Gece": {"16": 20}})
    monkeypatch.setattr(ddc, "set_value", lambda *args, **kwargs: pytest.fail("wrote a value"))
    assert cli.try_run(["--apply-preset", "Yok"]) == 1
    assert "unknown preset" in capsys.readouterr().err


def _freeze_now(monkeypatch, moment: datetime) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    monkeypatch.setattr(cli, "datetime", FrozenDatetime)


def test_schedule_apply_runs_due_preset_and_records_it(tmp_path, monkeypatch):
    _save_settings(
        tmp_path,
        monkeypatch,
        presets={"Gece": {"16": 20}},
        schedule=[{"time": "20:00", "preset": "Gece"}],
    )
    _freeze_now(monkeypatch, datetime(2026, 10, 27, 20, 0))
    calls: list[tuple[int, int]] = []

    def fake_set(code, value, **_kwargs):
        calls.append((code, value))
        return True

    monkeypatch.setattr(ddc, "set_value", fake_set)
    assert cli.try_run(["--schedule-apply"]) == 0
    assert calls == [(0x10, 20)]
    assert Settings.load().last_run == {"Gece": "2026-10-27"}

    # A second invocation on the same day does nothing.
    assert cli.try_run(["--schedule-apply"]) == 0
    assert calls == [(0x10, 20)]


def test_schedule_apply_skips_when_not_due(tmp_path, monkeypatch):
    _save_settings(
        tmp_path,
        monkeypatch,
        presets={"Gece": {"16": 20}},
        schedule=[{"time": "23:00", "preset": "Gece"}],
    )
    _freeze_now(monkeypatch, datetime(2026, 10, 27, 20, 0))
    monkeypatch.setattr(ddc, "set_value", lambda *args, **kwargs: pytest.fail("wrote a value"))
    assert cli.try_run(["--schedule-apply"]) == 0
    assert Settings.load().last_run == {}


def test_schedule_apply_failure_is_not_recorded(tmp_path, monkeypatch, capsys):
    _save_settings(
        tmp_path,
        monkeypatch,
        presets={"Gece": {"16": 20}},
        schedule=[{"time": "20:00", "preset": "Gece"}],
    )
    _freeze_now(monkeypatch, datetime(2026, 10, 27, 20, 0))
    monkeypatch.setattr(ddc, "set_value", lambda *args, **kwargs: False)
    assert cli.try_run(["--schedule-apply"]) == 1
    assert Settings.load().last_run == {}
    assert "scheduled preset" in capsys.readouterr().err


def test_menu_without_wofi_or_rofi_exits_one(tmp_path, monkeypatch, capsys):
    _save_settings(tmp_path, monkeypatch, presets={"Gece": {"16": 20}})
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.try_run(["--menu"]) == 1
    assert "wofi" in capsys.readouterr().err


def test_menu_applies_selected_preset(tmp_path, monkeypatch):
    _save_settings(tmp_path, monkeypatch, presets={"Gece": {"16": 20}})
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/wofi" if name == "wofi" else None,
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert kwargs["input"] == "Gece\n"
        return SimpleNamespace(returncode=0, stdout="Gece\n", stderr="")

    calls: list[tuple[int, int]] = []

    def fake_set(code, value, **_kwargs):
        calls.append((code, value))
        return True

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(ddc, "set_value", fake_set)
    assert cli.try_run(["--menu"]) == 0
    assert commands[0][:2] == ["/usr/bin/wofi", "--dmenu"]
    assert calls == [(0x10, 20)]


def test_menu_cancel_exits_one(tmp_path, monkeypatch):
    _save_settings(tmp_path, monkeypatch, presets={"Gece": {"16": 20}})
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/rofi" if name == "rofi" else None,
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    monkeypatch.setattr(ddc, "set_value", lambda *args, **kwargs: pytest.fail("wrote a value"))
    assert cli.try_run(["--menu"]) == 1


def test_multiple_actions_are_rejected(tmp_path, monkeypatch, capsys):
    _save_settings(tmp_path, monkeypatch)
    assert cli.try_run(["--list-presets", "--menu"]) == 2
    assert "one action" in capsys.readouterr().err


def test_daemon_dispatches(tmp_path, monkeypatch):
    from nitor import daemon

    _save_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon, "run_daemon", lambda: 7)
    assert cli.try_run(["--daemon"]) == 7
