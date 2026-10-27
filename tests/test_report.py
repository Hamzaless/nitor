"""Tests for diagnostics text and prefilled issue URLs."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from nitor import report


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_issue_url_encodes_title_and_body():
    url = report.issue_url("Crash on start & save", "Line one\nLine two: 100%")

    assert url.startswith("https://github.com/Hamzaless/nitor/issues/new?")
    query = _query(url)
    assert query["title"] == ["Crash on start & save"]
    assert query["body"] == ["Line one\nLine two: 100%"]
    assert "labels" not in query
    assert "%20" in url
    assert "%26" in url


def test_issue_url_encodes_non_ascii():
    url = report.issue_url("Bildschirm", "Größe: 27 Zoll")

    query = _query(url)
    assert query["title"] == ["Bildschirm"]
    assert query["body"] == ["Größe: 27 Zoll"]


def test_issue_url_joins_labels():
    url = report.issue_url("Title", "Body", ["bug", "monitor"])

    assert _query(url)["labels"] == ["bug,monitor"]


def test_issue_url_omits_empty_labels():
    url = report.issue_url("Title", "Body", ["", "  "])

    assert "labels" not in _query(url)


def test_diagnostics_text_contains_monitor_details():
    details = {
        "model": "U2720Q",
        "manufacturer": "Dell",
        "connector": "card0-DP-1",
        "serial": "ABC123",
        "resolution": "3840x2160",
        "refresh_hz": 59.94,
        "status": "connected",
        "modes": ["3840x2160", "2560x1440"],
        "panel_type": 3,
        "firmware": 0x10,
        "usage_minutes": 125,
        "vcp_version": 0x22,
        "ddcutil_version": "ddcutil 2.1.4",
    }

    text = report.diagnostics_text(
        details,
        "1.4.0",
        {"session_type": "wayland", "desktop": "GNOME"},
    )

    assert "Nitor 1.4.0" in text
    assert "Dell" in text
    assert "U2720Q" in text
    assert "card0-DP-1" in text
    assert "ABC123" in text
    assert "3840x2160" in text
    assert "59.94 Hz" in text
    assert "connected" in text
    assert "IPS" in text
    assert "0x10" in text
    assert "2 h 5 min" in text
    assert "2.2" in text
    assert "ddcutil 2.1.4" in text
    assert "3840x2160, 2560x1440" in text
    assert "session_type: wayland" in text
    assert "desktop: GNOME" in text
    assert "\n" in text


def test_diagnostics_text_handles_missing_values():
    text = report.diagnostics_text({}, "1.4.0", {})

    assert "Nitor 1.4.0" in text
    assert "—" in text
    assert "Session" not in text


def test_diagnostics_text_keeps_unknown_panel_value_raw():
    text = report.diagnostics_text({"panel_type": 0x09}, "1.4.0", {})

    assert "0x09" in text
