"""GTK 4 / libadwaita user interface for Nitor."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __app_id__, __version__, info, osd, report, search, updates  # noqa: E402
from .ddc import (  # noqa: E402
    DISPLAY_ORDER,
    FEATURES,
    DdcError,
    Display,
    DisplayInfo,
    available,
    ddcutil_version,
    format_value,
)
from .i18n import available_languages  # noqa: E402
from .i18n import init as init_i18n  # noqa: E402
from .i18n import translate as _  # noqa: E402
from .monitors import discover, stable_key  # noqa: E402
from .preset_io import export_json, import_json  # noqa: E402
from .presets import (  # noqa: E402
    CAPTURE_CODES,
    delete_preset,
    duplicate_preset,
    normalized,
    rename_preset,
)
from .settings import Settings  # noqa: E402
from .tray import TrayIcon  # noqa: E402

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {
    "en": "English",
    "tr": "Türkçe",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "ru": "Русский",
}

RESTORE_CODES = {0xDC, 0x14}
OSD_CODES = {0x10: "feature.brightness", 0x12: "feature.contrast", 0x62: "feature.volume"}

CONFIRM_VALUES: dict[tuple[int, int], tuple[str, str]] = {
    (0x8D, 0x01): ("confirm.blank.title", "confirm.blank.body"),
    (0xD6, 0x05): ("confirm.power.title", "confirm.power.body"),
}

VALUE_KEYS: dict[int, dict[int, str]] = {
    0xDC: {
        0x00: "value.display_mode.standard",
        0x01: "value.display_mode.productivity",
        0x02: "value.display_mode.mixed",
        0x03: "value.display_mode.movie",
        0x04: "value.display_mode.user",
        0x05: "value.display_mode.games",
        0x06: "value.display_mode.sports",
    },
    0x86: {
        0x02: "value.scaling.full",
        0x0B: "value.scaling.aspect",
        0x0D: "value.scaling.1to1",
    },
    0x8D: {0x01: "value.blank.on", 0x02: "value.blank.off"},
    0xD6: {0x01: "value.power.dpm_on", 0x04: "value.power.dpm_off", 0x05: "value.power.off"},
}

PANEL_TYPES = {1: "CRT", 2: "LCD", 3: "LED", 4: "OLED", 5: "Plasma"}


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "nitor" / "cache.json"


def _value_label(code: int, value: int, caps: dict[int, str]) -> str:
    key = VALUE_KEYS.get(code, {}).get(value)
    if key:
        return _(key)
    return caps.get(value) or f"0x{value:02X}"


def _readonly_text(code: int, value: int | None) -> str:
    if value is None:
        return "—"
    if code == 0xC0:
        hours, minutes = divmod(value, 60)
        return _("info.usage_time", hours=hours, minutes=minutes)
    return format_value(code, value)


@dataclass
class FeatureRow:
    code: int
    kind: str
    values: dict[int, str] = field(default_factory=dict)
    row: Adw.ActionRow | Adw.ComboRow | None = None
    scale: Gtk.Scale | None = None
    combo: Adw.ComboRow | None = None


class MonitorWindow(Adw.ApplicationWindow):
    """The main window: controls, details and compact views."""

    def __init__(self, app: Adw.Application, settings: Settings):
        super().__init__(application=app, title=_("window.title"))
        self.set_default_size(640, 860)
        self.set_icon_name(__app_id__)

        self._settings = settings
        self._display: Display | None = None
        self._displays: list[Display] = []
        self._rows: dict[int, FeatureRow] = {}
        self._group_rows: dict[str, list[Gtk.Widget]] = {}
        self._cache: dict[str, list[int | None]] = {}
        self._cache_key = ""
        self._timers: dict[int, int] = {}
        self._compact_scales: dict[int, Gtk.Scale] = {}
        self._detail_rows: dict[str, Adw.ActionRow] = {}
        self._details_data: dict = {}
        self._details_built = False
        self._loading = False
        self._loading_displays = False
        self._ready = False
        self._busy = False
        self._monitor: DisplayInfo | None = None
        self._groups: dict[str, Adw.PreferencesGroup] = {}
        self._preset_names: list[str] = []
        self._auto_hide = settings.auto_hide
        self._debounce_ms = settings.debounce_ms
        self._active_handler: int | None = None
        self._was_active = False
        self._dialogs_open = 0
        self._release_url = ""

        # ---- header -------------------------------------------------------
        self._view_stack = Adw.ViewStack()
        self._view_switcher = Adw.ViewSwitcher()
        self._view_switcher.set_stack(self._view_stack)
        self._view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar()
        header.set_title_widget(self._view_switcher)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        header.pack_start(self._spinner)

        self._monitor_dropdown = Gtk.DropDown.new_from_strings(["-"])
        self._monitor_dropdown.set_visible(False)
        self._monitor_dropdown.set_tooltip_text(_("monitor.select"))
        self._monitor_dropdown.connect("notify::selected", self._on_monitor_selected)
        header.pack_start(self._monitor_dropdown)

        self._refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_button.set_tooltip_text(_("tooltip.refresh"))
        self._refresh_button.connect("clicked", lambda *_: self.reload())
        header.pack_end(self._refresh_button)

        menu = Gio.Menu()
        menu.append(_("menu.settings"), "app.settings")
        menu.append(_("menu.compact"), "app.compact")
        menu.append(_("menu.check_updates"), "app.check-updates")
        menu.append(_("menu.refresh"), "win.refresh")
        menu.append(_("menu.about"), "app.about")
        self._menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(self._menu_button)

        self._banner = Adw.Banner()
        self._banner.set_revealed(False)
        self._banner.connect("button-clicked", self._open_release)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self._banner)
        toolbar.set_content(self._stack)

        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(toolbar)

        self._overlay = Gtk.Overlay()
        self._overlay.set_child(self._toasts)
        self._build_busy_overlay()
        self._overlay.add_overlay(self._busy_page)
        self.set_content(self._overlay)

        self._install_actions()
        if self._auto_hide:
            self._active_handler = self.connect("notify::is-active", self._on_active_changed)
        self._show_loading()
        threading.Thread(target=self._bootstrap, daemon=True).start()

    # ------------------------------------------------------------- actions --
    def _install_actions(self) -> None:
        refresh = Gio.SimpleAction.new("refresh", None)
        refresh.connect("activate", lambda *_: self.reload())
        self.add_action(refresh)

    # ------------------------------------------------------------ auto hide --
    def _on_active_changed(self, *_args) -> None:
        if self.is_active():
            self._was_active = True
            return
        if self._busy or not self._was_active or self._has_open_dialog():
            return
        GLib.timeout_add(250, self._hide_if_unfocused)

    def _track_dialog(self, dialog) -> None:
        self._dialogs_open += 1
        released = {"done": False}

        def release(*_args) -> None:
            if released["done"]:
                return
            released["done"] = True
            self._dialogs_open = max(0, self._dialogs_open - 1)
            GLib.timeout_add(250, self._hide_if_unfocused)

        for signal in ("closed", "response", "close-request"):
            with contextlib.suppress(TypeError):
                dialog.connect(signal, release)

    def _has_open_dialog(self) -> bool:
        if self._dialogs_open > 0:
            return True
        application = self.get_application()
        return application is not None and len(application.get_windows()) > 1

    def _hide_if_unfocused(self) -> bool:
        if not self.is_active() and not self._has_open_dialog() and not self._busy:
            self.hide()
        return False

    def _set_auto_hide(self, enabled: bool) -> None:
        self._auto_hide = enabled
        if enabled and self._active_handler is None:
            self._active_handler = self.connect("notify::is-active", self._on_active_changed)
        elif not enabled and self._active_handler is not None:
            self.disconnect(self._active_handler)
            self._active_handler = None

    # --------------------------------------------------------------- states --
    def _show_loading(self) -> None:
        page = Adw.StatusPage(title=_("status.loading"), icon_name="video-display-symbolic")
        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_size_request(32, 32)
        spinner.set_margin_top(12)
        page.set_child(spinner)
        self._stack.add_named(page, "loading")
        self._stack.set_visible_child_name("loading")
        self._spinner.start()
        self._spinner.set_visible(True)

    def _show_error(self, title: str, body: str) -> None:
        self._spinner.stop()
        self._spinner.set_visible(False)
        page = Adw.StatusPage(title=title, description=body, icon_name="dialog-warning-symbolic")
        button = Gtk.Button(label=_("menu.quit"))
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", lambda *_: self.get_application().quit())
        page.set_child(button)
        self._stack.add_named(page, "error")
        self._stack.set_visible_child_name("error")

    # ------------------------------------------------------------ bootstrap --
    def _bootstrap(self) -> None:
        try:
            if not available():
                GLib.idle_add(self._show_error, _("error.ddcutil.title"), _("error.ddcutil.body"))
                return
            displays = discover()
            if not displays:
                GLib.idle_add(self._show_error, _("error.monitor.title"), _("error.monitor.body"))
                return
            display = self._pick_display(displays)
            caps = display.capabilities()
        except DdcError as exc:
            log.warning("DDC bootstrap failed: %s", exc)
            GLib.idle_add(self._show_error, _("error.ddc.title"), _("error.ddc.body"))
            return
        GLib.idle_add(self._build_content, displays, display, caps)

    def _pick_display(self, displays: list[Display]) -> Display:
        wanted = self._settings.monitor_key
        if wanted:
            for display in displays:
                if stable_key(display.info) == wanted:
                    return display
        return displays[0]

    def _build_content(self, displays: list[Display], display: Display, caps: dict) -> bool:
        self._displays = displays
        self._display = display
        self._monitor = display.info
        self._cache_key = stable_key(display.info)
        self._settings.monitor_key = self._cache_key
        self._settings.save()

        self._populate_monitor_dropdown()
        self._build_controls_page(caps)
        self._build_compact_page(caps)
        self._build_details_page()

        self._stack.add_named(self._view_stack, "main")
        self._stack.set_visible_child_name("main")
        self._view_stack.set_visible_child_name("controls")
        self._load_cache()
        self.reload()
        return False

    def _populate_monitor_dropdown(self) -> None:
        labels = [item.info.label for item in self._displays]
        self._loading_displays = True
        self._monitor_dropdown.set_model(Gtk.StringList.new(labels))
        current = 0
        for index, item in enumerate(self._displays):
            if stable_key(item.info) == self._cache_key:
                current = index
                break
        self._monitor_dropdown.set_selected(current)
        self._monitor_dropdown.set_visible(len(self._displays) > 1)
        self._loading_displays = False

    def _on_monitor_selected(self, dropdown: Gtk.DropDown, _pspec) -> None:
        if self._loading_displays:
            return
        index = dropdown.get_selected()
        if not 0 <= index < len(self._displays):
            return
        target = self._displays[index]
        if self._monitor is not None and stable_key(target.info) == stable_key(self._monitor):
            return
        self._save_profile()
        self._settings.monitor_key = stable_key(target.info)
        self._settings.save()
        application = self.get_application()
        if application is not None:
            application.reload_ui(monitor_key=self._settings.monitor_key, apply_profile=True)
        osd.show(target.info.label)

    # ---------------------------------------------------------- controls page -
    def _build_controls_page(self, caps: dict) -> None:
        page = Adw.PreferencesPage()

        self._presets_group = Adw.PreferencesGroup(title=_("preset.title"))
        page.add(self._presets_group)
        self._build_presets_group()

        self._groups = {}
        self._group_rows = {}
        for key in ("category.image", "category.audio", "category.input", "category.osd",
                    "category.info", "category.actions"):
            group = Adw.PreferencesGroup(title=_(key))
            page.add(group)
            self._groups[key] = group
            self._group_rows[key] = []

        for code in (code for code in DISPLAY_ORDER if code in caps and code in FEATURES):
            meta = FEATURES[code]
            feature = FeatureRow(code=code, kind=meta.kind, values=dict(caps.get(code, {})))
            self._rows[code] = feature
            group = self._groups[meta.category]
            title = _(meta.key)

            if meta.kind == "slider":
                row = Adw.ActionRow(title=title)
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
                scale.set_digits(0)
                scale.set_size_request(240, -1)
                scale.set_draw_value(True)
                scale.set_value_pos(Gtk.PositionType.RIGHT)
                scale.set_tooltip_text(title)
                scale.connect("value-changed", self._on_scale_changed, code)
                row.add_suffix(scale)
                feature.row, feature.scale = row, scale
                group.add(row)

            elif meta.kind == "enum":
                combo = Adw.ComboRow(title=title)
                labels = [
                    _value_label(code, value, feature.values)
                    for value in sorted(feature.values)
                ]
                combo.set_model(Gtk.StringList.new(labels or ["—"]))
                combo.connect("notify::selected", self._on_combo_changed, code)
                feature.row, feature.combo = combo, combo
                group.add(combo)

            elif meta.kind == "action":
                row = Adw.ActionRow(title=title, subtitle=_("action.warning"))
                button = Gtk.Button(label=_("action.apply"))
                button.add_css_class("destructive-action")
                button.connect("clicked", self._on_action_clicked, code, title)
                row.add_suffix(button)
                feature.row = row
                group.add(row)

            else:
                row = Adw.ActionRow(title=title, subtitle="…")
                feature.row = row
                group.add(row)

            self._group_rows[meta.category].append(feature.row)

        if self._rows:
            hint = Gtk.Label(label=_("hint.mode_lock"), wrap=True, xalign=0)
            hint.add_css_class("dim-label")
            hint.set_margin_top(10)
            hint.set_margin_start(12)
            hint.set_margin_end(12)
            self._groups["category.image"].add(hint)
            self._group_rows["category.image"].append(hint)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text(_("search.placeholder"))
        self._search_entry.set_margin_top(6)
        self._search_entry.set_margin_bottom(6)
        self._search_entry.set_margin_start(12)
        self._search_entry.set_margin_end(12)
        self._search_entry.connect("search-changed", self._on_search_changed)

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        container.append(self._search_entry)
        container.append(page)

        self._view_stack.add_titled_with_icon(
            container, "controls", _("view.controls"), "video-display-symbolic"
        )

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text()
        if not query.strip():
            for rows in self._group_rows.values():
                for row in rows:
                    row.set_visible(True)
            for group in self._groups.values():
                group.set_visible(True)
            self._presets_group.set_visible(True)
            return

        names = {code: _(meta.key) for code, meta in FEATURES.items()}
        allowed = set(search.filter_codes(query, names))
        self._presets_group.set_visible(False)
        for key, rows in self._group_rows.items():
            any_visible = False
            for row in rows:
                code = next(
                    (c for c, f in self._rows.items() if f.row is row), None
                )
                visible = code is not None and code in allowed
                row.set_visible(visible)
                any_visible = any_visible or visible
            self._groups[key].set_visible(any_visible or key == "category.image")

    # ----------------------------------------------------------- compact page -
    def _build_compact_page(self, caps: dict) -> None:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=_("compact.title"))
        page.add(group)

        self._compact_scales = {}
        self._compact_pending: list[tuple[Gtk.Scale, int]] = []
        for code, key in ((0x10, "feature.brightness"), (0x12, "feature.contrast"),
                          (0x62, "feature.volume")):
            if code not in self._rows:
                continue
            row = Adw.ActionRow(title=_(key))
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            scale.set_digits(0)
            scale.set_size_request(260, -1)
            scale.set_draw_value(True)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            row.add_suffix(scale)
            group.add(row)
            self._compact_scales[code] = scale
            self._compact_pending.append((scale, code))

        presets_row = Adw.ActionRow(title=_("preset.title"))
        button = Gtk.Button(label=_("view.controls"))
        button.add_css_class("pill")
        button.connect("clicked", lambda *_: self._view_stack.set_visible_child_name("controls"))
        presets_row.add_suffix(button)
        group.add(presets_row)

        self._view_stack.add_titled_with_icon(
            page, "compact", _("view.compact"), "view-restore-symbolic"
        )

    # ----------------------------------------------------------- details page -
    def _build_details_page(self) -> None:
        page = Adw.PreferencesPage()
        self._detail_rows = {}

        hero = Adw.PreferencesGroup()
        hero_row = Adw.ActionRow(title=_("details.loading"))
        hero_row.add_prefix(Gtk.Image.new_from_icon_name("video-display-symbolic"))
        hero_row.set_subtitle("")
        hero.add(hero_row)
        page.add(hero)
        self._detail_rows["hero"] = hero_row

        for key, title in (
            ("signal", _("details.signal")),
            ("identity", _("details.identity")),
            ("usage", _("details.usage")),
            ("system", _("details.system")),
        ):
            group = Adw.PreferencesGroup(title=title)
            page.add(group)
            self._detail_rows["group_" + key] = group

        actions = Adw.PreferencesGroup(title=_("details.actions"))
        page.add(actions)

        copy_row = Adw.ActionRow(title=_("details.copy"), subtitle=_("details.copy_desc"))
        copy_button = Gtk.Button(label=_("settings.copy"))
        copy_button.add_css_class("pill")
        copy_button.connect("clicked", lambda *_: self._copy_details())
        copy_row.add_suffix(copy_button)
        actions.add(copy_row)

        report_row = Adw.ActionRow(title=_("details.report"), subtitle=_("details.report_desc"))
        report_button = Gtk.Button(label=_("details.open"))
        report_button.add_css_class("pill")
        report_button.connect("clicked", lambda *_: self._report_issue())
        report_row.add_suffix(report_button)
        actions.add(report_row)

        self._view_stack.add_titled_with_icon(
            page, "details", _("view.details"), "dialog-information-symbolic"
        )
        threading.Thread(target=self._collect_details, daemon=True).start()

    def _collect_details(self) -> None:
        assert self._display is not None
        connector = self._display.info.connector
        label = self._display.info.label
        try:
            data = info.collect(connector, label, self._display.info.bus)
        except Exception as exc:  # never break the window
            log.debug("info.collect failed: %s", exc)
            data = {}
        # DDC fields must come from THIS display, not the module-level first one.
        for code, key in ((0xB6, "panel_type"), (0xC9, "firmware"), (0xC0, "usage_minutes"),
                          (0xDF, "vcp_version"), (0xAE, "vertical_freq")):
            try:
                current, _maximum = self._display.get(code)
            except Exception:
                current = None
            data[key] = current
        vertical = data.get("vertical_freq")
        if isinstance(vertical, int) and vertical > 0:
            data["refresh_hz"] = round(vertical / 100, 2)
        data["ddcutil_version"] = ddcutil_version()
        self._details_data = data
        GLib.idle_add(self._apply_details, data)

    def _apply_details(self, data: dict) -> bool:
        if self._details_built:
            return False
        self._details_built = True
        assert self._monitor is not None
        hero = self._detail_rows["hero"]
        hero.set_title(self._monitor.label)
        hero.set_subtitle(
            f"{data.get('manufacturer') or self._monitor.manufacturer} · "
            f"{data.get('connector') or self._monitor.connector}"
        )

        def add(group_key: str, title: str, value) -> None:
            text = str(value) if value not in (None, "") else _("details.unknown")
            row = Adw.ActionRow(title=title, subtitle=text)
            self._detail_rows["group_" + group_key].add(row)

        add("signal", _("details.resolution"), data.get("resolution"))
        add("signal", _("details.refresh"), f"{data['refresh_hz']} Hz" if data.get("refresh_hz") else "")
        add("signal", _("details.connection"), data.get("connector"))
        add("signal", _("details.status"), data.get("status"))

        add("identity", _("details.serial"), data.get("serial"))
        panel = data.get("panel_type")
        add("identity", _("details.panel_type"), PANEL_TYPES.get(panel, panel))
        add("identity", _("details.firmware"), data.get("firmware"))
        add("identity", _("details.vcp"), data.get("vcp_version"))

        minutes = data.get("usage_minutes")
        add("usage", _("details.usage_time"), info.format_usage(minutes) if minutes is not None else "")

        add("system", _("details.ddcutil"), data.get("ddcutil_version"))
        add("system", _("details.modes"), len(data.get("modes") or []))
        add("system", _("details.nitor"), __version__)
        return False

    def _copy_details(self) -> None:
        assert self._monitor is not None
        text = report.diagnostics_text(
            self._details_data or {
                "model": self._monitor.model,
                "manufacturer": self._monitor.manufacturer,
                "connector": self._monitor.connector,
                "serial": self._monitor.serial,
            },
            __version__,
            {"session": os.environ.get("XDG_SESSION_TYPE", ""), "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "")},
        )
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)
            self._toast(_("settings.copied"))

    def _report_issue(self) -> None:
        assert self._monitor is not None
        body = report.diagnostics_text(
            self._details_data or {
                "model": self._monitor.model,
                "manufacturer": self._monitor.manufacturer,
                "connector": self._monitor.connector,
                "serial": self._monitor.serial,
            },
            __version__,
            {"session": os.environ.get("XDG_SESSION_TYPE", ""), "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "")},
        )
        url = report.issue_url(_("details.report_title"), body)
        Gio.AppInfo.launch_default_for_uri(url, None)

    # -------------------------------------------------------------- presets --
    def _build_presets_group(self) -> None:
        self._preset_names = []
        self._preset_dropdown = Gtk.DropDown.new_from_strings([_("preset.none")])
        self._preset_dropdown.set_sensitive(False)

        self._preset_apply = Gtk.Button(label=_("preset.apply"))
        self._preset_apply.add_css_class("pill")
        self._preset_apply.set_sensitive(False)
        self._preset_apply.connect("clicked", lambda *_: self._apply_selected_preset())

        manage_button = Gtk.Button(icon_name="emblem-system-symbolic")
        manage_button.add_css_class("flat")
        manage_button.set_tooltip_text(_("preset.manage"))
        manage_button.connect("clicked", lambda *_: self.show_preset_manager())

        select_row = Adw.ActionRow(title=_("preset.select"))
        select_row.add_suffix(self._preset_dropdown)
        select_row.add_suffix(self._preset_apply)
        select_row.add_suffix(manage_button)
        self._presets_group.add(select_row)

        save_row = Adw.ActionRow(title=_("preset.save_title"), subtitle=_("preset.save_subtitle"))
        save_button = Gtk.Button(label=_("preset.save"))
        save_button.add_css_class("pill")
        save_button.connect("clicked", lambda *_: self._save_preset())
        save_row.add_suffix(save_button)
        self._presets_group.add(save_row)

    def _populate_presets(self) -> None:
        self._preset_names = sorted(self._settings.presets)
        if self._preset_names:
            self._preset_dropdown.set_model(Gtk.StringList.new(self._preset_names))
            self._preset_dropdown.set_selected(0)
            self._preset_dropdown.set_sensitive(True)
            self._preset_apply.set_sensitive(True)
        else:
            self._preset_dropdown.set_model(Gtk.StringList.new([_("preset.none")]))
            self._preset_dropdown.set_sensitive(False)
            self._preset_apply.set_sensitive(False)

    def _apply_selected_preset(self) -> None:
        if self._busy or not self._preset_names:
            return
        index = self._preset_dropdown.get_selected()
        if not 0 <= index < len(self._preset_names):
            return
        self.apply_preset_by_name(self._preset_names[index])

    def apply_preset_by_name(self, name: str) -> None:
        raw = self._settings.presets.get(name)
        if not raw:
            return
        try:
            values = {int(code): int(value) for code, value in raw.items()}
        except (TypeError, ValueError):
            return
        self._apply_preset(values)

    def _apply_preset(self, values: dict[int, int]) -> None:
        if self._display is None:
            return
        ordered: list[tuple[int, int]] = []
        for code, value in normalized(values):
            if code not in self._rows:
                continue
            current = self._cache.get(str(code), [None, None])[0]
            if current == value:
                continue
            ordered.append((code, value))
        if not ordered:
            self._toast(_("preset.applied"))
            return

        for code, value in ordered:
            maximum = self._cache.get(str(code), [None, None])[1]
            self._apply(code, value, maximum)

        codes = tuple(code for code, _value in ordered)
        self._preset_apply.set_sensitive(False)
        self._show_busy(len(ordered))

        def run() -> None:
            ok = True
            total = len(ordered)
            for index, (code, value) in enumerate(ordered, start=1):
                GLib.idle_add(self._update_busy, index, total, code)
                assert self._display is not None
                if not self._display.set_value(code, value, sleep_multiplier="0.1"):
                    ok = False
            GLib.idle_add(self._finish_preset, codes, ok)

        threading.Thread(target=run, daemon=True).start()

    def _finish_preset(self, codes: tuple[int, ...], ok: bool) -> bool:
        if self._preset_names:
            self._preset_apply.set_sensitive(True)
        self._finish_busy(ok)
        self._refresh_codes(codes)
        if ok:
            osd.show(_("preset.applied"))
        return False

    def _save_preset(self) -> None:
        captured: dict[str, int] = {}
        for code in CAPTURE_CODES:
            if code not in self._rows:
                continue
            current = self._cache.get(str(code), [None, None])[0]
            if current is not None:
                captured[str(code)] = current
        if not captured:
            self._toast(_("error.write_failed"))
            return

        dialog = Adw.AlertDialog(heading=_("preset.save_title"), body=_("preset.name_hint"))
        entry = Gtk.Entry(placeholder_text=_("preset.name_placeholder"))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("action.cancel"))
        dialog.add_response("save", _("preset.save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        self._track_dialog(dialog)

        def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "save":
                return
            name = entry.get_text().strip()[:60]
            if not name:
                return
            self._settings.presets[name] = captured
            self._settings.save()
            self._populate_presets()
            if name in self._preset_names:
                self._preset_dropdown.set_selected(self._preset_names.index(name))
            self._toast(_("preset.saved", name=name))

        dialog.connect("response", on_response)
        dialog.present(self)

    def show_preset_manager(self) -> None:
        dialog = Adw.PreferencesDialog()
        page = Adw.PreferencesPage()
        dialog.add(page)
        self._manager_group = Adw.PreferencesGroup(title=_("preset.manage_title"))
        page.add(self._manager_group)
        self._manager_rows: list[Gtk.Widget] = []

        io_group = Adw.PreferencesGroup(title=_("preset.io"))
        page.add(io_group)

        import_row = Adw.ActionRow(title=_("preset.import"), subtitle=_("preset.import_desc"))
        import_button = Gtk.Button(label=_("preset.choose_file"))
        import_button.add_css_class("pill")
        import_button.connect("clicked", lambda *_: self._import_presets())
        import_row.add_suffix(import_button)
        io_group.add(import_row)

        export_row = Adw.ActionRow(title=_("preset.export"), subtitle=_("preset.export_desc"))
        export_button = Gtk.Button(label=_("preset.save_file"))
        export_button.add_css_class("pill")
        export_button.connect("clicked", lambda *_: self._export_presets())
        export_row.add_suffix(export_button)
        io_group.add(export_row)

        self._populate_manager()
        self._track_dialog(dialog)
        dialog.present(self)

    def _populate_manager(self) -> None:
        for row in self._manager_rows:
            self._manager_group.remove(row)
        self._manager_rows.clear()

        for name in sorted(self._settings.presets):
            row = Adw.ActionRow(title=name, subtitle=_("preset.values_count", count=len(self._settings.presets[name])))
            rename = Gtk.Button(icon_name="document-edit-symbolic")
            rename.add_css_class("flat")
            rename.set_tooltip_text(_("preset.rename"))
            rename.connect("clicked", lambda *_, n=name: self._rename_preset(n))
            duplicate = Gtk.Button(icon_name="edit-copy-symbolic")
            duplicate.add_css_class("flat")
            duplicate.set_tooltip_text(_("preset.duplicate"))
            duplicate.connect("clicked", lambda *_, n=name: self._duplicate_preset(n))
            remove = Gtk.Button(icon_name="user-trash-symbolic")
            remove.add_css_class("flat")
            remove.set_tooltip_text(_("preset.delete"))
            remove.connect("clicked", lambda *_, n=name: self._delete_preset(n))
            row.add_suffix(rename)
            row.add_suffix(duplicate)
            row.add_suffix(remove)
            self._manager_group.add(row)
            self._manager_rows.append(row)

    def _rename_preset(self, name: str) -> None:
        dialog = Adw.AlertDialog(heading=_("preset.rename"), body=_("preset.name_hint"))
        entry = Gtk.Entry(text=name)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("action.cancel"))
        dialog.add_response("save", _("preset.save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        self._track_dialog(dialog)

        def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "save":
                return
            try:
                self._settings.presets = rename_preset(self._settings.presets, name, entry.get_text())
            except (KeyError, ValueError) as exc:
                self._toast(str(exc))
                return
            self._settings.save()
            self._populate_presets()
            self._populate_manager()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _duplicate_preset(self, name: str) -> None:
        try:
            self._settings.presets = duplicate_preset(self._settings.presets, name)
        except (KeyError, ValueError) as exc:
            self._toast(str(exc))
            return
        self._settings.save()
        self._populate_presets()
        self._populate_manager()

    def _delete_preset(self, name: str) -> None:
        try:
            self._settings.presets = delete_preset(self._settings.presets, name)
        except KeyError:
            return
        self._settings.save()
        self._populate_presets()
        self._populate_manager()

    def _export_presets(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title(_("preset.export"))
        dialog.set_initial_name("nitor-presets.json")
        self._dialogs_open += 1

        def on_done(file_dialog, result) -> None:
            self._dialogs_open = max(0, self._dialogs_open - 1)
            try:
                gfile = file_dialog.save_finish(result)
            except GLib.Error:
                return
            path = gfile.get_path()
            if not path:
                return
            try:
                Path(path).write_text(
                    export_json(self._settings.presets), encoding="utf-8"
                )
                self._toast(_("preset.exported"))
            except (OSError, ValueError) as exc:
                self._toast(str(exc))

        dialog.save(self, None, on_done)

    def _import_presets(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title(_("preset.import"))
        self._dialogs_open += 1

        def on_done(file_dialog, result) -> None:
            self._dialogs_open = max(0, self._dialogs_open - 1)
            try:
                gfile = file_dialog.open_finish(result)
            except GLib.Error:
                return
            path = gfile.get_path()
            if not path:
                return
            try:
                imported = import_json(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._toast(str(exc))
                return
            self._settings.presets.update(imported)
            self._settings.save()
            self._populate_presets()
            self._populate_manager()
            self._toast(_("preset.imported", count=len(imported)))

        dialog.open(self, None, on_done)

    # ------------------------------------------------------------- settings --
    def show_settings(self) -> None:
        dialog = Adw.PreferencesDialog()
        page = Adw.PreferencesPage()
        dialog.add(page)

        group = Adw.PreferencesGroup(title=_("settings.title"))
        page.add(group)

        languages = ["system", *available_languages()]
        labels = [_("settings.language.system"), *[LANGUAGE_NAMES.get(code, code) for code in languages[1:]]]
        language_row = Adw.ComboRow(title=_("settings.language"))
        language_row.set_model(Gtk.StringList.new(labels))
        current = self._settings.language if self._settings.language in languages else "system"
        language_row.set_selected(languages.index(current))
        language_row.connect("notify::selected", self._on_language_selected, languages)
        group.add(language_row)

        auto_hide_row = Adw.SwitchRow(title=_("settings.auto_hide"), subtitle=_("settings.auto_hide_desc"))
        auto_hide_row.set_active(self._auto_hide)
        auto_hide_row.connect("notify::active", self._on_auto_hide_toggled)
        group.add(auto_hide_row)

        debounce_row = Adw.SpinRow.new_with_range(50, 2000, 50)
        debounce_row.set_title(_("settings.debounce"))
        debounce_row.set_subtitle(_("settings.debounce_desc"))
        debounce_row.set_value(self._debounce_ms)
        debounce_row.connect("notify::value", self._on_debounce_changed)
        group.add(debounce_row)

        updates_row = Adw.SwitchRow(title=_("settings.check_updates"), subtitle=_("settings.check_updates_desc"))
        updates_row.set_active(self._settings.check_updates)
        updates_row.connect("notify::active", self._on_check_updates_toggled)
        group.add(updates_row)

        diagnostics_row = Adw.ActionRow(title=_("settings.diagnostics"), subtitle=_("settings.diagnostics_desc"))
        diagnostics_button = Gtk.Button(label=_("settings.copy"))
        diagnostics_button.add_css_class("pill")
        diagnostics_button.connect("clicked", lambda *_: self._copy_diagnostics())
        diagnostics_row.add_suffix(diagnostics_button)
        group.add(diagnostics_row)

        schedule_group = Adw.PreferencesGroup(title=_("schedule.title"))
        page.add(schedule_group)
        for entry in self._settings.schedule:
            row = Adw.ActionRow(
                title=_("schedule.entry", time=entry.get("time", ""), preset=entry.get("preset", "")),
            )
            remove = Gtk.Button(icon_name="user-trash-symbolic")
            remove.add_css_class("flat")
            remove.connect("clicked", lambda *_, item=entry: self._remove_schedule(item))
            row.add_suffix(remove)
            schedule_group.add(row)

        add_row = Adw.ActionRow(title=_("schedule.add"), subtitle=_("schedule.add_desc"))
        add_button = Gtk.Button(label=_("schedule.add_button"))
        add_button.add_css_class("pill")
        add_button.connect("clicked", lambda *_: self._add_schedule())
        add_row.add_suffix(add_button)
        schedule_group.add(add_row)

        enable_row = Adw.SwitchRow(title=_("schedule.enable"), subtitle=_("schedule.enable_desc"))
        enable_row.set_active(self._settings.schedule_enabled)
        enable_row.connect("notify::active", self._on_schedule_toggled)
        schedule_group.add(enable_row)

        self._settings_dialog = dialog
        self._track_dialog(dialog)
        dialog.present(self)

    def _on_language_selected(self, row: Adw.ComboRow, _pspec, languages: list[str]) -> None:
        index = row.get_selected()
        if not 0 <= index < len(languages):
            return
        language = languages[index]
        if language == self._settings.language:
            return
        self._settings.language = language
        self._settings.save()
        application = self.get_application()
        if application is not None:
            application.reload_ui(language=language)

    def _on_auto_hide_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        self._settings.auto_hide = row.get_active()
        self._settings.save()
        self._set_auto_hide(row.get_active())

    def _on_debounce_changed(self, row: Adw.SpinRow, _pspec) -> None:
        value = int(row.get_value())
        self._debounce_ms = value
        self._settings.debounce_ms = value
        self._settings.save()

    def _on_check_updates_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        self._settings.check_updates = row.get_active()
        self._settings.save()

    def _copy_diagnostics(self) -> None:
        def run() -> None:
            lines = [
                f"Nitor {__version__}",
                f"Monitor: {self._monitor.label if self._monitor is not None else '-'}",
                f"ddcutil: {ddcutil_version() or '-'}",
                f"Session: {os.environ.get('XDG_SESSION_TYPE', '-')} / "
                f"{os.environ.get('XDG_CURRENT_DESKTOP', '-')}",
                f"System: {platform.platform()}",
            ]
            GLib.idle_add(self._set_clipboard, "\n".join(lines))

        threading.Thread(target=run, daemon=True).start()

    def _set_clipboard(self, text: str) -> bool:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)
            self._toast(_("settings.copied"))
        return False

    def _add_schedule(self) -> None:
        dialog = Adw.AlertDialog(heading=_("schedule.add"), body=_("schedule.time_hint"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        time_entry = Gtk.Entry(placeholder_text="20:00", text="20:00")
        preset_names = sorted(self._settings.presets)
        preset_dropdown = Gtk.DropDown.new_from_strings(preset_names or [_("preset.none")])
        box.append(time_entry)
        box.append(preset_dropdown)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("action.cancel"))
        dialog.add_response("add", _("schedule.add_button"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        self._track_dialog(dialog)

        def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "add" or not preset_names:
                return
            index = preset_dropdown.get_selected()
            if not 0 <= index < len(preset_names):
                return
            entry = {"time": time_entry.get_text().strip(), "preset": preset_names[index]}
            if not re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d$", entry["time"]):
                self._toast(_("schedule.bad_time"))
                return
            self._settings.schedule.append(entry)
            self._settings.save()
            self._toast(_("schedule.added"))
            dialog.close()
            self._reopen_settings()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _remove_schedule(self, entry: dict) -> None:
        try:
            self._settings.schedule.remove(entry)
        except ValueError:
            return
        self._settings.save()
        self._toast(_("schedule.removed"))
        self._reopen_settings()

    def _reopen_settings(self) -> None:
        dialog = getattr(self, "_settings_dialog", None)
        if dialog is not None:
            with contextlib.suppress(Exception):
                dialog.close()
        self._settings_dialog = None
        GLib.timeout_add(200, lambda: (self.show_settings(), False)[1])

    def _on_schedule_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        enabled = row.get_active()
        self._settings.schedule_enabled = enabled
        self._settings.save()
        self._set_schedule_unit(enabled)

    def _set_schedule_unit(self, enabled: bool) -> None:
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        service = unit_dir / "nitor-schedule.service"
        timer = unit_dir / "nitor-schedule.timer"
        executable = shutil.which("nitor") or "nitor"
        try:
            if enabled:
                unit_dir.mkdir(parents=True, exist_ok=True)
                service.write_text(
                    "[Unit]\nDescription=Nitor scheduled presets\n\n"
                    f"[Service]\nType=oneshot\nExecStart={executable} --schedule-apply\n",
                    encoding="utf-8",
                )
                timer.write_text(
                    "[Unit]\nDescription=Apply Nitor presets on a timer\n\n"
                    "[Timer]\nOnCalendar=*:0/5\nPersistent=true\n\n"
                    "[Install]\nWantedBy=timers.target\n",
                    encoding="utf-8",
                )
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=10)
                subprocess.run(
                    ["systemctl", "--user", "enable", "--now", "nitor-schedule.timer"],
                    check=False,
                    timeout=10,
                )
                self._toast(_("schedule.enabled"))
            else:
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", "nitor-schedule.timer"],
                    check=False,
                    timeout=10,
                )
                self._toast(_("schedule.disabled"))
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("schedule unit failed: %s", exc)
            self._toast(_("schedule.error"))

    # ------------------------------------------------------------ busy overlay -
    def _build_busy_overlay(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(".nitor-busy { background-color: alpha(@window_bg_color, 0.98); }")
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        self._busy_page = Adw.StatusPage()
        self._busy_page.add_css_class("nitor-busy")
        self._busy_page.set_hexpand(True)
        self._busy_page.set_vexpand(True)
        self._busy_page.set_can_target(True)
        self._busy_page.set_visible(False)
        self._busy_page.set_icon_name("video-display-symbolic")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_halign(Gtk.Align.CENTER)
        self._busy_spinner = Gtk.Spinner()
        self._busy_spinner.set_size_request(36, 36)
        self._busy_progress = Gtk.ProgressBar()
        self._busy_progress.set_size_request(300, -1)
        content.append(self._busy_spinner)
        content.append(self._busy_progress)
        self._busy_page.set_child(content)

    def _show_busy(self, total: int) -> None:
        self._busy = True
        self._busy_page.set_title(_("preset.preparing"))
        self._busy_page.set_description("")
        self._busy_progress.set_fraction(0.0)
        self._busy_spinner.start()
        self._refresh_button.set_sensitive(False)
        self._menu_button.set_sensitive(False)
        self._busy_page.set_visible(True)

    def _update_busy(self, index: int, total: int, code: int) -> bool:
        meta = FEATURES.get(code)
        name = _(meta.key) if meta is not None else f"0x{code:02X}"
        self._busy_page.set_description(_("preset.step", feature=name))
        self._busy_progress.set_fraction(min(1.0, index / max(1, total)))
        return False

    def _finish_busy(self, ok: bool) -> bool:
        self._busy_spinner.stop()
        self._busy_progress.set_fraction(1.0)
        self._busy_page.set_title(_("preset.applied") if ok else _("error.write_failed"))
        self._busy_page.set_description("")
        GLib.timeout_add(700, self._hide_busy)
        return False

    def _hide_busy(self) -> bool:
        self._busy_page.set_visible(False)
        self._busy = False
        self._refresh_button.set_sensitive(True)
        self._menu_button.set_sensitive(True)
        return False

    # ------------------------------------------------------------ read/write --
    def reload(self) -> None:
        if not self._rows or self._display is None:
            return
        self._spinner.start()
        self._spinner.set_visible(True)
        threading.Thread(target=self._read_all, daemon=True).start()

    def _read_all(self) -> None:
        if self._display is None:
            return
        values = self._display.read_many(list(self._rows))
        for code, (current, maximum) in values.items():
            GLib.idle_add(self._apply, code, current, maximum)
        GLib.idle_add(self._finish_load)

    def _finish_load(self) -> bool:
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._ready = True
        if not getattr(self, "_compact_connected", False):
            for scale, code in self._compact_pending:
                scale.connect("value-changed", self._on_scale_changed, code)
            self._compact_connected = True
        self._save_cache()
        self._maybe_check_updates()
        application = self.get_application()
        if application is not None and getattr(application, "_apply_profile", False):
            application._apply_profile = False
            profile = self._settings.profiles.get(self._cache_key)
            if profile:
                try:
                    values = {int(code): int(value) for code, value in profile.items()}
                except (TypeError, ValueError):
                    values = {}
                if values:
                    self._apply_preset(values)
        return False

    def _apply(self, code: int, current: int | None, maximum: int | None) -> bool:
        feature = self._rows.get(code)
        if feature is None:
            return False
        self._loading = True
        try:
            if feature.scale is not None:
                if maximum:
                    feature.scale.set_range(0, maximum)
                if current is not None:
                    feature.scale.set_value(current)
            elif feature.combo is not None and current is not None:
                if current not in feature.values:
                    feature.values[current] = _value_label(code, current, {})
                    labels = [
                        _value_label(code, value, feature.values)
                        for value in sorted(feature.values)
                    ]
                    feature.combo.set_model(Gtk.StringList.new(labels))
                feature.combo.set_selected(sorted(feature.values).index(current))
            elif feature.row is not None:
                feature.row.set_subtitle(_readonly_text(code, current))

            compact = self._compact_scales.get(code)
            if compact is not None:
                if maximum:
                    compact.set_range(0, maximum)
                if current is not None:
                    compact.set_value(current)
        finally:
            self._loading = False
        self._cache[str(code)] = [current, maximum]
        return False

    def _on_scale_changed(self, scale: Gtk.Scale, code: int) -> None:
        if self._loading or not self._ready or self._busy:
            return
        previous = self._timers.pop(code, None)
        if previous:
            GLib.source_remove(previous)
        value = int(round(scale.get_value()))
        if self._cache.get(str(code), [None])[0] == value:
            return
        self._timers[code] = GLib.timeout_add(self._debounce_ms, self._flush_scale, code, value)

    def _flush_scale(self, code: int, value: int) -> bool:
        self._timers.pop(code, None)
        threading.Thread(
            target=self._write, args=(code, value), kwargs={"verify": True}, daemon=True
        ).start()
        return False

    def _on_combo_changed(self, combo: Adw.ComboRow, _pspec, code: int) -> None:
        if self._loading or not self._ready or self._busy:
            return
        feature = self._rows.get(code)
        if feature is None:
            return
        index = combo.get_selected()
        values = sorted(feature.values)
        if not 0 <= index < len(values):
            return
        value = values[index]
        confirmation = CONFIRM_VALUES.get((code, value))
        if confirmation:
            self._confirm_write(code, value, *confirmation)
            return
        threading.Thread(target=self._write, args=(code, value), daemon=True).start()

    def _confirm_write(self, code: int, value: int, title_key: str, body_key: str) -> None:
        dialog = Adw.MessageDialog(transient_for=self, heading=_(title_key), body=_(body_key))
        dialog.add_response("cancel", _("action.cancel"))
        dialog.add_response("apply", _("action.apply"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        self._track_dialog(dialog)

        def on_response(_dialog: Adw.MessageDialog, response: str) -> None:
            if response == "apply":
                threading.Thread(target=self._write, args=(code, value), daemon=True).start()
            else:
                current = self._cache.get(str(code), [None, None])[0]
                if current is not None:
                    GLib.idle_add(self._apply, code, current, None)

        dialog.connect("response", on_response)
        dialog.present()

    def _write(self, code: int, value: int, *, verify: bool = False) -> None:
        if self._display is None:
            return
        saved: dict[int, int] = {}
        if code in RESTORE_CODES:
            for companion in (0x10, 0x12):
                current, _maximum = self._display.get(companion)
                if current is not None:
                    saved[companion] = current

        ok = self._display.set_value(code, value)
        locked = False
        if ok and verify:
            current, _maximum = self._display.get(code)
            if current is not None and current != value:
                ok = False
                locked = True
                meta = FEATURES.get(code)
                name = _(meta.key) if meta is not None else f"0x{code:02X}"
                GLib.idle_add(self._toast, _("error.locked", feature=name))
                GLib.idle_add(self._apply, code, current, None)

        if saved:
            time.sleep(2.0)
            for companion, previous in saved.items():
                self._display.set_value(companion, previous)

        if not ok and not locked:
            GLib.idle_add(self._toast, _("error.write_failed"))
        if ok and code in OSD_CODES:
            osd.show(f"{_(OSD_CODES[code])}: {value}")
        GLib.idle_add(self._refresh_codes, (code, *saved.keys()))

    def _refresh_codes(self, codes: tuple[int, ...]) -> bool:
        if self._display is None:
            return False

        def run() -> None:
            assert self._display is not None
            values = self._display.read_many(list(codes))
            for code, (current, maximum) in values.items():
                GLib.idle_add(self._apply, code, current, maximum)

        threading.Thread(target=run, daemon=True).start()
        return False

    def _on_action_clicked(self, _button: Gtk.Button, code: int, title: str) -> None:
        dialog = Adw.MessageDialog(transient_for=self, heading=title, body=_("action.body"))
        dialog.add_response("cancel", _("action.cancel"))
        dialog.add_response("apply", _("action.apply"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        self._track_dialog(dialog)

        def on_response(_dialog: Adw.MessageDialog, response: str) -> None:
            if response != "apply":
                return

            def run() -> None:
                if self._display is None:
                    return
                ok = self._display.set_value(code, 0)
                GLib.idle_add(self._toast, _("action.done") if ok else _("error.write_failed"))
                GLib.idle_add(self._refresh_all_delayed)

            threading.Thread(target=run, daemon=True).start()

        dialog.connect("response", on_response)
        dialog.present()

    def _refresh_all_delayed(self) -> bool:
        GLib.timeout_add(2000, lambda: (self.reload(), False)[1])
        return False

    # --------------------------------------------------------------- toasts --
    def _toast(self, message: str) -> bool:
        self._toasts.add_toast(Adw.Toast.new(message))
        return False

    # ---------------------------------------------------------------- cache --
    def _load_cache(self) -> None:
        try:
            data = json.loads(_cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if self._cache_key in data and isinstance(data[self._cache_key], dict):
            self._cache = data[self._cache_key]
        elif all(str(k).isdigit() for k in data):
            self._cache = data
        else:
            self._cache = data.get(self._cache_key, {})
        for key, value in self._cache.items():
            try:
                code = int(key)
                current, maximum = value[0], value[1]
            except (TypeError, ValueError, IndexError):
                continue
            if code in self._rows:
                self._apply(code, current, maximum)

    def _save_cache(self) -> None:
        path = _cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict) or all(str(k).isdigit() for k in data):
                data = {}
            data[self._cache_key] = self._cache
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError as exc:
            log.debug("Could not write cache: %s", exc)

    def _save_profile(self) -> None:
        if not self._cache_key:
            return
        profile: dict[str, int] = {}
        for code in CAPTURE_CODES:
            current = self._cache.get(str(code), [None, None])[0]
            if current is not None:
                profile[str(code)] = current
        if profile:
            self._settings.profiles[self._cache_key] = profile
            self._settings.save()

    # -------------------------------------------------------------- updates --
    def _update_cache_path(self) -> Path:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
        return Path(base) / "nitor" / "update.json"

    def _read_update_cache(self) -> dict:
        try:
            data = json.loads(self._update_cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_update_cache(self, release: updates.Release) -> None:
        path = self._update_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"checked": time.time(), "latest": release.version, "url": release.url}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _maybe_check_updates(self) -> None:
        cache = self._read_update_cache()
        latest = cache.get("latest")
        url = cache.get("url")
        if isinstance(latest, str) and isinstance(url, str) and updates.is_newer(latest, __version__):
            self._show_update_banner(latest, url)
        if not self._settings.check_updates:
            return
        checked = cache.get("checked", 0)
        if isinstance(checked, (int, float)) and time.time() - checked < 86400:
            return
        threading.Thread(target=self._update_worker, args=(False,), daemon=True).start()

    def check_updates(self) -> None:
        self._toast(_("update.checking"))
        threading.Thread(target=self._update_worker, args=(True,), daemon=True).start()

    def _update_worker(self, manual: bool) -> None:
        try:
            release = updates.latest_release()
        except Exception as exc:
            log.debug("Update check failed: %s", exc)
            if manual:
                GLib.idle_add(self._toast, _("update.error"))
            return
        if release is None:
            if manual:
                GLib.idle_add(self._toast, _("update.error"))
            return
        self._write_update_cache(release)
        if updates.is_newer(release.version, __version__):
            GLib.idle_add(self._show_update_banner, release.version, release.url)
            if manual:
                GLib.idle_add(self._toast, _("update.available_title", version=release.version))
        elif manual:
            GLib.idle_add(self._toast, _("update.up_to_date"))

    def _show_update_banner(self, version: str, url: str) -> bool:
        self._release_url = url
        self._banner.set_title(_("update.available_title", version=version))
        self._banner.set_button_label(_("update.view"))
        self._banner.set_revealed(True)
        return False

    def _open_release(self, *_args) -> None:
        if self._release_url:
            Gio.AppInfo.launch_default_for_uri(self._release_url, None)

    # ------------------------------------------------------------- hotplug --
    def start_hotplug(self) -> None:
        GLib.timeout_add_seconds(15, self._poll_displays)

    def _poll_displays(self) -> bool:
        if not self.get_visible() or self._busy:
            return True

        def run() -> None:
            try:
                displays = discover()
            except Exception:
                return
            GLib.idle_add(self._on_displays_changed, displays)

        threading.Thread(target=run, daemon=True).start()
        return True

    def _on_displays_changed(self, displays: list[Display]) -> bool:
        old_keys = [stable_key(item.info) for item in self._displays]
        new_keys = [stable_key(item.info) for item in displays]
        if old_keys == new_keys:
            return False
        self._displays = displays
        self._populate_monitor_dropdown()
        if self._cache_key not in new_keys and displays:
            self._settings.monitor_key = stable_key(displays[0].info)
            self._settings.save()
            application = self.get_application()
            if application is not None:
                application.reload_ui(monitor_key=self._settings.monitor_key)
        return False


class NitorApplication(Adw.Application):
    """Application object with a single window and an optional tray icon."""

    def __init__(self, settings: Settings):
        super().__init__(application_id=__app_id__, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._settings = settings
        self._window: MonitorWindow | None = None
        self._tray: TrayIcon | None = None
        self._pending_monitor_key = ""
        self._apply_profile = False
        self.connect("startup", self._on_startup)
        self.connect("shutdown", self._on_shutdown)

    def _on_startup(self, _app: Adw.Application) -> None:
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about)
        self.add_action(about)

        settings = Gio.SimpleAction.new("settings", None)
        settings.connect("activate", lambda *_: self._settings_action())
        self.add_action(settings)

        check_updates = Gio.SimpleAction.new("check-updates", None)
        check_updates.connect("activate", lambda *_: self._check_updates_action())
        self.add_action(check_updates)

        compact = Gio.SimpleAction.new("compact", None)
        compact.connect("activate", lambda *_: self._compact_action())
        self.add_action(compact)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def _settings_action(self) -> None:
        if self._window is not None:
            self._window.show_settings()

    def _check_updates_action(self) -> None:
        if self._window is not None:
            self._window.check_updates()

    def _compact_action(self) -> None:
        if self._window is None:
            return
        stack = self._window._view_stack
        stack.set_visible_child_name(
            "controls" if stack.get_visible_child_name() == "compact" else "compact"
        )

    def _on_about(self, *_args) -> None:
        dialog = Adw.AboutDialog(
            application_name="Nitor",
            application_icon=__app_id__,
            version=__version__,
            developer_name="Hamzaless",
            comments=_("about.comments"),
            website="https://github.com/Hamzaless/nitor",
            issue_url="https://github.com/Hamzaless/nitor/issues",
            license_type=Gtk.License.MIT_X11,
            developers=["Hamzaless https://github.com/Hamzaless"],
        )
        dialog.add_link(_("about.website"), "https://github.com/Hamzaless/nitor")
        dialog.add_link(_("about.issues"), "https://github.com/Hamzaless/nitor/issues")
        if self._window is not None:
            self._window._track_dialog(dialog)
        dialog.present(self._window or self.get_active_window())

    def reload_ui(self, language: str | None = None, monitor_key: str | None = None,
                  apply_profile: bool = False) -> None:
        if language is not None:
            init_i18n(None if language == "system" else language)
        if monitor_key is not None:
            self._pending_monitor_key = monitor_key
        self._apply_profile = apply_profile
        previous = self._window
        self._window = None
        if previous is not None:
            previous.destroy()
        self._settings.monitor_key = self._pending_monitor_key or self._settings.monitor_key
        self._window = MonitorWindow(self, self._settings)
        self._window.start_hotplug()
        self._window.present()

    def _ensure_tray(self) -> None:
        if self._tray is not None:
            return
        try:
            tray = TrayIcon(
                presets=lambda: sorted(self._settings.presets),
                on_preset=self._tray_preset,
                on_open=self._tray_open,
                on_quit=self.quit,
            )
            if tray.start():
                self._tray = tray
        except Exception as exc:
            log.debug("Tray unavailable: %s", exc)

    def _tray_preset(self, name: str) -> None:
        GLib.idle_add(self._tray_preset_ui, name)

    def _tray_preset_ui(self, name: str) -> bool:
        if self._window is None:
            self.activate()
        if self._window is not None:
            self._window.apply_preset_by_name(name)
        return False

    def _tray_open(self) -> None:
        self.activate()
        if self._window is not None:
            self._window.present()

    def _on_shutdown(self, _app: Adw.Application) -> None:
        if self._tray is not None:
            self._tray.stop()
            self._tray = None

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MonitorWindow(self, self._settings)
            self._window.start_hotplug()
        self._window.present()
        self._ensure_tray()
