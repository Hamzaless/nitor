"""GTK 4 / libadwaita user interface for Nitor."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __app_id__, __version__, updates  # noqa: E402
from .ddc import (  # noqa: E402
    DISPLAY_ORDER,
    FEATURES,
    DdcError,
    Monitor,
    available,
    capabilities,
    ddcutil_version,
    detect,
    format_value,
    get,
    read_many,
    set_value,
)
from .i18n import available_languages  # noqa: E402
from .i18n import init as init_i18n  # noqa: E402
from .i18n import translate as _  # noqa: E402
from .presets import CAPTURE_CODES, normalized  # noqa: E402
from .settings import Settings  # noqa: E402

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {
    "de": "Deutsch",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "ru": "Русский",
    "tr": "Türkçe",
}

#: Features that reset picture settings on many monitors.
RESTORE_CODES = {0xDC, 0x14}

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
    0x8D: {
        0x01: "value.blank.on",
        0x02: "value.blank.off",
    },
    0xD6: {
        0x01: "value.power.dpm_on",
        0x04: "value.power.dpm_off",
        0x05: "value.power.off",
    },
}


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
    """Main window: presets, one group per feature category and settings."""

    def __init__(self, app: Adw.Application, settings: Settings):
        super().__init__(application=app, title=_("window.title"))
        self.set_default_size(560, 820)
        self.set_icon_name(__app_id__)

        self._settings = settings
        self._rows: dict[int, FeatureRow] = {}
        self._cache: dict[str, list[int | None]] = {}
        self._timers: dict[int, int] = {}
        self._loading = False
        self._busy = False
        self._ready = False
        self._monitor: Monitor | None = None
        self._groups: dict[str, Adw.PreferencesGroup] = {}
        self._auto_hide = settings.auto_hide
        self._debounce_ms = settings.debounce_ms
        self._active_handler: int | None = None
        self._was_active = False
        self._dialogs_open = 0

        self._title = Adw.WindowTitle(title=_("window.title"), subtitle="")
        header = Adw.HeaderBar()
        header.set_title_widget(self._title)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        header.pack_start(self._spinner)

        self._refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_button.set_tooltip_text(_("tooltip.refresh"))
        self._refresh_button.connect("clicked", lambda *_: self.reload())
        header.pack_end(self._refresh_button)

        menu = Gio.Menu()
        menu.append(_("menu.settings"), "app.settings")
        menu.append(_("menu.check_updates"), "app.check-updates")
        menu.append(_("menu.refresh"), "win.refresh")
        menu.append(_("menu.about"), "app.about")
        self._menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(self._menu_button)

        self._banner = Adw.Banner()
        self._banner.set_revealed(False)
        self._banner.connect("button-clicked", self._open_release)
        self._release_url = ""

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
        self._overlay.add_overlay(self._busy_box)
        self.set_content(self._overlay)

        self._install_actions()
        if self._auto_hide:
            self._active_handler = self.connect("notify::is-active", self._on_active_changed)
        self._show_loading()
        threading.Thread(target=self._bootstrap, daemon=True).start()

    # -------------------------------------------------------------- actions --
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
        """Keep the panel alive while a dialog is open.

        libadwaita dialogs (Adw.Dialog) are widgets, not Gtk.Windows, so they do
        not show up in Gtk.Application.get_windows(). Count them explicitly.
        """
        self._dialogs_open += 1
        released = {"done": False}

        def release(*_args) -> None:
            if released["done"]:
                return
            released["done"] = True
            self._dialogs_open = max(0, self._dialogs_open - 1)
            # Give focus a moment to return to the panel before deciding.
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
        if not self.is_active() and not self._has_open_dialog():
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
            monitors = detect()
            if not monitors:
                GLib.idle_add(self._show_error, _("error.monitor.title"), _("error.monitor.body"))
                return
            monitor = monitors[0]
            caps = capabilities()
        except DdcError as exc:
            log.warning("DDC bootstrap failed: %s", exc)
            GLib.idle_add(self._show_error, _("error.ddc.title"), _("error.ddc.body"))
            return
        GLib.idle_add(self._build_content, monitor, caps)

    def _build_content(self, monitor: Monitor, caps: dict[int, dict[int, str]]) -> bool:
        self._monitor = monitor
        self._title.set_subtitle(monitor.label)

        page = Adw.PreferencesPage()

        self._presets_group = Adw.PreferencesGroup(title=_("preset.title"))
        page.add(self._presets_group)
        self._build_presets_group()

        self._groups = {}
        for key in ("category.image", "category.audio", "category.input", "category.osd",
                    "category.info", "category.actions"):
            group = Adw.PreferencesGroup(title=_(key))
            page.add(group)
            self._groups[key] = group

        known = [code for code in DISPLAY_ORDER if code in caps and code in FEATURES]
        for code in known:
            meta = FEATURES[code]
            feature_row = FeatureRow(code=code, kind=meta.kind, values=dict(caps.get(code, {})))
            self._rows[code] = feature_row
            group = self._groups[meta.category]
            title = _(meta.key)

            if meta.kind == "slider":
                action_row = Adw.ActionRow(title=title)
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
                scale.set_digits(0)
                scale.set_size_request(250, -1)
                scale.set_draw_value(True)
                scale.set_value_pos(Gtk.PositionType.RIGHT)
                scale.set_tooltip_text(title)
                scale.connect("value-changed", self._on_scale_changed, code)
                action_row.add_suffix(scale)
                feature_row.row = action_row
                feature_row.scale = scale
                group.add(action_row)

            elif meta.kind == "enum":
                combo = Adw.ComboRow(title=title)
                labels = [
                    _value_label(code, value, feature_row.values)
                    for value in sorted(feature_row.values)
                ]
                combo.set_model(Gtk.StringList.new(labels or ["—"]))
                combo.connect("notify::selected", self._on_combo_changed, code)
                feature_row.row = combo
                feature_row.combo = combo
                group.add(combo)

            elif meta.kind == "action":
                action_row = Adw.ActionRow(title=title)
                action_row.set_subtitle(_("action.warning"))
                button = Gtk.Button(label=_("action.apply"))
                button.add_css_class("destructive-action")
                button.connect("clicked", self._on_action_clicked, code, title)
                action_row.add_suffix(button)
                feature_row.row = action_row
                group.add(action_row)

            else:  # readonly
                action_row = Adw.ActionRow(title=title)
                action_row.set_subtitle("…")
                feature_row.row = action_row
                group.add(action_row)

        if known:
            hint = Gtk.Label(label=_("hint.mode_lock"), wrap=True, xalign=0)
            hint.add_css_class("dim-label")
            hint.set_margin_top(10)
            hint.set_margin_start(12)
            hint.set_margin_end(12)
            self._groups["category.image"].add(hint)

        if not self._rows:
            self._show_error(_("error.monitor.title"), _("error.monitor.body"))
            return False

        self._populate_presets()
        self._stack.add_named(page, "content")
        self._stack.set_visible_child_name("content")
        self._load_cache()
        self.reload()
        return False

    # ----------------------------------------------------------- busy overlay --
    def _build_busy_overlay(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(
            ".nitor-busy { background-color: alpha(@window_bg_color, 0.98); }"
        )
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        self._busy_page = Adw.StatusPage()
        self._busy_page.add_css_class("nitor-busy")
        self._busy_page.set_hexpand(True)
        self._busy_page.set_vexpand(True)
        self._busy_page.set_halign(Gtk.Align.FILL)
        self._busy_page.set_valign(Gtk.Align.FILL)
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

        self._busy_box = self._busy_page

    def _show_busy(self, total: int) -> None:
        self._busy = True
        self._busy_total = max(1, total)
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

    # -------------------------------------------------------------- presets --
    def _build_presets_group(self) -> None:
        self._preset_names: list[str] = []

        self._preset_dropdown = Gtk.DropDown.new_from_strings([_("preset.none")])
        self._preset_dropdown.set_sensitive(False)

        self._preset_apply = Gtk.Button(label=_("preset.apply"))
        self._preset_apply.add_css_class("pill")
        self._preset_apply.set_sensitive(False)
        self._preset_apply.connect("clicked", lambda *_: self._apply_selected_preset())

        select_row = Adw.ActionRow(title=_("preset.select"))
        select_row.add_suffix(self._preset_dropdown)
        select_row.add_suffix(self._preset_apply)
        self._presets_group.add(select_row)

        save_row = Adw.ActionRow(
            title=_("preset.save_title"),
            subtitle=_("preset.save_subtitle"),
        )
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
        raw = self._settings.presets.get(self._preset_names[index])
        if not raw:
            return
        try:
            values = {int(code): int(value) for code, value in raw.items()}
        except (TypeError, ValueError):
            return
        self._apply_preset(values)

    def _apply_preset(self, values: dict[int, int]) -> None:
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

        # Move the sliders immediately so the user sees what is happening.
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
                if not set_value(code, value, sleep_multiplier="0.1"):
                    ok = False
            GLib.idle_add(self._finish_preset, codes, ok)

        threading.Thread(target=run, daemon=True).start()

    def _finish_preset(self, codes: tuple[int, ...], ok: bool) -> bool:
        if self._preset_names:
            self._preset_apply.set_sensitive(True)
        self._finish_busy(ok)
        self._refresh_codes(codes)
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
        except Exception as exc:  # network failures must never break the UI
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

        auto_hide_row = Adw.SwitchRow(
            title=_("settings.auto_hide"),
            subtitle=_("settings.auto_hide_desc"),
        )
        auto_hide_row.set_active(self._auto_hide)
        auto_hide_row.connect("notify::active", self._on_auto_hide_toggled)
        group.add(auto_hide_row)

        debounce_row = Adw.SpinRow.new_with_range(50, 2000, 50)
        debounce_row.set_title(_("settings.debounce"))
        debounce_row.set_subtitle(_("settings.debounce_desc"))
        debounce_row.set_value(self._debounce_ms)
        debounce_row.connect("notify::value", self._on_debounce_changed)
        group.add(debounce_row)

        updates_row = Adw.SwitchRow(
            title=_("settings.check_updates"),
            subtitle=_("settings.check_updates_desc"),
        )
        updates_row.set_active(self._settings.check_updates)
        updates_row.connect("notify::active", self._on_check_updates_toggled)
        group.add(updates_row)

        diagnostics_row = Adw.ActionRow(
            title=_("settings.diagnostics"),
            subtitle=_("settings.diagnostics_desc"),
        )
        diagnostics_button = Gtk.Button(label=_("settings.copy"))
        diagnostics_button.add_css_class("pill")
        diagnostics_button.connect("clicked", lambda *_: self._copy_diagnostics())
        diagnostics_row.add_suffix(diagnostics_button)
        group.add(diagnostics_row)

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
            application.reload_ui(language)

    def _on_auto_hide_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        enabled = row.get_active()
        self._settings.auto_hide = enabled
        self._settings.save()
        self._set_auto_hide(enabled)

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

    def _on_debounce_changed(self, row: Adw.SpinRow, _pspec) -> None:
        value = int(row.get_value())
        self._debounce_ms = value
        self._settings.debounce_ms = value
        self._settings.save()

    # ------------------------------------------------------------ read/write -
    def reload(self) -> None:
        if not self._rows:
            return
        self._spinner.start()
        self._spinner.set_visible(True)
        threading.Thread(target=self._read_all, daemon=True).start()

    def _read_all(self) -> None:
        values = read_many(list(self._rows))
        for code, (current, maximum) in values.items():
            GLib.idle_add(self._apply, code, current, maximum)
        GLib.idle_add(self._finish_load)

    def _finish_load(self) -> bool:
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._ready = True
        self._save_cache()
        self._maybe_check_updates()
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
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_(title_key),
            body=_(body_key),
        )
        dialog.add_response("cancel", _("action.cancel"))
        dialog.add_response("apply", _("action.apply"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog: Adw.MessageDialog, response: str) -> None:
            if response == "apply":
                threading.Thread(target=self._write, args=(code, value), daemon=True).start()
            else:
                current = self._cache.get(str(code), [None, None])[0]
                if current is not None:
                    GLib.idle_add(self._apply, code, current, None)

        dialog.connect("response", on_response)
        self._track_dialog(dialog)
        dialog.present()

    def _write(self, code: int, value: int, *, verify: bool = False) -> None:
        saved: dict[int, int] = {}
        if code in RESTORE_CODES:
            for companion in (0x10, 0x12):
                current, _maximum = get(companion)
                if current is not None:
                    saved[companion] = current

        ok = set_value(code, value)
        locked = False
        if ok and verify:
            current, _maximum = get(code)
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
                set_value(companion, previous)

        if not ok and not locked:
            GLib.idle_add(self._toast, _("error.write_failed"))
        GLib.idle_add(self._refresh_codes, (code, *saved.keys()))

    def _refresh_codes(self, codes: tuple[int, ...]) -> bool:
        def run() -> None:
            values = read_many(list(codes))
            for code, (current, maximum) in values.items():
                GLib.idle_add(self._apply, code, current, maximum)

        threading.Thread(target=run, daemon=True).start()
        return False

    def _on_action_clicked(self, _button: Gtk.Button, code: int, title: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=_("action.body"),
        )
        dialog.add_response("cancel", _("action.cancel"))
        dialog.add_response("apply", _("action.apply"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog: Adw.MessageDialog, response: str) -> None:
            if response != "apply":
                return

            def run() -> None:
                ok = set_value(code, 0)
                GLib.idle_add(self._toast, _("action.done") if ok else _("error.write_failed"))
                GLib.idle_add(self._refresh_all_delayed)

            threading.Thread(target=run, daemon=True).start()

        dialog.connect("response", on_response)
        self._track_dialog(dialog)
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
        path = _cache_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for key, value in data.items():
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
            path.write_text(json.dumps(self._cache), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - disk issues
            log.debug("Could not write cache: %s", exc)


class NitorApplication(Adw.Application):
    """Adw.Application subclass with single instance behaviour."""

    def __init__(self, settings: Settings):
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._settings = settings
        self._window: MonitorWindow | None = None
        self.connect("startup", self._on_startup)

    def _on_startup(self, _app: Adw.Application) -> None:
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about)
        self.add_action(about)

        check_updates = Gio.SimpleAction.new("check-updates", None)
        check_updates.connect(
            "activate",
            lambda *_: self._window.check_updates() if self._window is not None else None,
        )
        self.add_action(check_updates)

        settings = Gio.SimpleAction.new("settings", None)
        settings.connect(
            "activate",
            lambda *_: self._window.show_settings() if self._window is not None else None,
        )
        self.add_action(settings)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

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

    def reload_ui(self, language: str | None = None) -> None:
        """Recreate the window, optionally after switching language."""
        if language is not None:
            init_i18n(None if language == "system" else language)
        previous = self._window
        self._window = None
        if previous is not None:
            previous.destroy()
        self._window = MonitorWindow(self, self._settings)
        self._window.present()

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MonitorWindow(self, self._settings)
        self._window.present()
