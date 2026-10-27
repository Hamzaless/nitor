"""GTK 4 / libadwaita user interface for Nitor."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __app_id__, __version__  # noqa: E402
from .ddc import (  # noqa: E402
    DISPLAY_ORDER,
    FEATURES,
    DdcError,
    Monitor,
    available,
    capabilities,
    detect,
    format_value,
    get,
    set_value,
)
from .i18n import translate as _  # noqa: E402

log = logging.getLogger(__name__)

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
    """Main window: builds one group per feature category."""

    def __init__(self, app: Adw.Application, auto_hide: bool = True):
        super().__init__(application=app, title=_("window.title"))
        self._auto_hide = auto_hide
        self._was_active = False
        self.set_default_size(560, 780)
        self.set_icon_name(__app_id__)

        self._rows: dict[int, FeatureRow] = {}
        self._cache: dict[str, list[int | None]] = {}
        self._timers: dict[int, int] = {}
        self._loading = False
        self._ready = False
        self._monitor: Monitor | None = None
        self._groups: dict[str, Adw.PreferencesGroup] = {}

        self._title = Adw.WindowTitle(title=_("window.title"), subtitle="")
        header = Adw.HeaderBar()
        header.set_title_widget(self._title)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        header.pack_start(self._spinner)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text(_("tooltip.refresh"))
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_end(refresh)

        menu = Gio.Menu()
        menu.append(_("menu.refresh"), "win.refresh")
        menu.append(_("menu.about"), "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_button)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._stack)

        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        self._install_actions()
        if self._auto_hide:
            self.connect("notify::is-active", self._on_active_changed)
        self._show_loading()
        threading.Thread(target=self._bootstrap, daemon=True).start()

    # ------------------------------------------------------------- auto hide --
    def _on_active_changed(self, *_args) -> None:
        """Hide the panel when the user clicks somewhere else.

        Dialogs count as extra application windows, so an open confirmation
        dialog never dismisses the panel underneath it.
        """
        if self.is_active():
            self._was_active = True
            return
        if not self._was_active or self._has_open_dialog():
            return
        GLib.timeout_add(250, self._hide_if_unfocused)

    def _has_open_dialog(self) -> bool:
        application = self.get_application()
        return application is not None and len(application.get_windows()) > 1

    def _hide_if_unfocused(self) -> bool:
        if not self.is_active() and not self._has_open_dialog():
            self.hide()
        return False

    # ------------------------------------------------------------- actions --
    def _install_actions(self) -> None:
        refresh = Gio.SimpleAction.new("refresh", None)
        refresh.connect("activate", lambda *_: self.reload())
        self.add_action(refresh)

    # ------------------------------------------------------------- states ---
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
                labels = [_value_label(code, value, feature_row.values) for value in sorted(feature_row.values)]
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

        if "category.image" in self._groups and known:
            hint = Gtk.Label(label=_("hint.mode_lock"), wrap=True, xalign=0)
            hint.add_css_class("dim-label")
            hint.set_margin_top(10)
            hint.set_margin_start(12)
            hint.set_margin_end(12)
            self._groups["category.image"].add(hint)

        if not self._rows:
            self._show_error(_("error.monitor.title"), _("error.monitor.body"))
            return False

        self._stack.add_named(page, "content")
        self._stack.set_visible_child_name("content")
        self._load_cache()
        self.reload()
        return False

    # ------------------------------------------------------------ read/write -
    def reload(self) -> None:
        if not self._rows:
            return
        self._spinner.start()
        self._spinner.set_visible(True)
        threading.Thread(target=self._read_all, daemon=True).start()

    def _read_all(self) -> None:
        for code in list(self._rows):
            current, maximum = get(code)
            GLib.idle_add(self._apply, code, current, maximum)
        GLib.idle_add(self._finish_load)

    def _finish_load(self) -> bool:
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._ready = True
        self._save_cache()
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
        if self._loading or not self._ready:
            return
        previous = self._timers.pop(code, None)
        if previous:
            GLib.source_remove(previous)
        value = int(round(scale.get_value()))
        self._timers[code] = GLib.timeout_add(300, self._flush_scale, code, value)

    def _flush_scale(self, code: int, value: int) -> bool:
        self._timers.pop(code, None)
        threading.Thread(target=self._write, args=(code, value), daemon=True).start()
        return False

    def _on_combo_changed(self, combo: Adw.ComboRow, _pspec, code: int) -> None:
        if self._loading or not self._ready:
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
            self._confirm_write(code, value, feature, *confirmation)
            return
        threading.Thread(target=self._write, args=(code, value), daemon=True).start()

    def _confirm_write(self, code: int, value: int, feature: FeatureRow,
                       title_key: str, body_key: str) -> None:
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
        dialog.present()

    def _write(self, code: int, value: int) -> None:
        """Write a value, restoring brightness/contrast when a mode changes."""
        saved: dict[int, int] = {}
        if code in RESTORE_CODES:
            for companion in (0x10, 0x12):
                current, _maximum = get(companion)
                if current is not None:
                    saved[companion] = current
        ok = set_value(code, value)
        if saved:
            time.sleep(2.0)
            for companion, previous in saved.items():
                set_value(companion, previous)
        if not ok:
            GLib.idle_add(self._toast, _("error.write_failed"))
        GLib.idle_add(self._refresh_codes, (code, *saved.keys()))

    def _refresh_codes(self, codes: tuple[int, ...]) -> bool:
        def run() -> None:
            for code in codes:
                current, maximum = get(code)
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

    def __init__(self, auto_hide: bool = True) -> None:
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._auto_hide = auto_hide
        self._window: MonitorWindow | None = None
        self.connect("startup", self._on_startup)

    def _on_startup(self, _app: Adw.Application) -> None:
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about)
        self.add_action(about)
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
        dialog.present(self._window or self.get_active_window())

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MonitorWindow(self, auto_hide=self._auto_hide)
        self._window.present()
