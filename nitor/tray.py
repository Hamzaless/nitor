"""Optional StatusNotifierItem tray icon.

``gi`` is imported inside the methods, never at module import time, so the
rest of Nitor (and the test suite) keeps working on systems without
PyGObject.  The tray is a convenience: :meth:`TrayIcon.start` returns False
instead of raising when there is no session bus, no StatusNotifierWatcher or
no PyGObject at all.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable
from typing import Any

__all__ = ["TrayIcon", "build_menu"]

log = logging.getLogger(__name__)

SNI_INTERFACE = "org.kde.StatusNotifierItem"
SNI_PATH = "/StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"

SNI_XML = f"""
<node>
  <interface name="{SNI_INTERFACE}">
    <method name="ContextMenu">
      <arg type="i" direction="in"/>
      <arg type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg type="i" direction="in"/>
      <arg type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" direction="in"/>
      <arg type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" direction="in"/>
      <arg type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewStatus">
      <arg type="s"/>
    </signal>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewMenu"/>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
  </interface>
</node>
"""

MENU_XML = f"""
<node>
  <interface name="{MENU_INTERFACE}">
    <method name="GetLayout">
      <arg type="i" direction="in" name="parentId"/>
      <arg type="i" direction="in" name="recursionDepth"/>
      <arg type="as" direction="in" name="propertyNames"/>
      <arg type="u" direction="out" name="revision"/>
      <arg type="(ia{{sv}}av)" direction="out" name="layout"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in" name="ids"/>
      <arg type="as" direction="in" name="propertyNames"/>
      <arg type="a(ia{{sv}})" direction="out" name="properties"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in" name="id"/>
      <arg type="s" direction="in" name="name"/>
      <arg type="v" direction="out" name="value"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in" name="id"/>
      <arg type="s" direction="in" name="eventId"/>
      <arg type="v" direction="in" name="data"/>
      <arg type="u" direction="in" name="timestamp"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in" name="events"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" direction="in" name="id"/>
      <arg type="b" direction="out" name="needUpdate"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in" name="ids"/>
      <arg type="ai" direction="out" name="updatesNeeded"/>
      <arg type="ai" direction="out" name="idErrors"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{{sv}})"/>
      <arg type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u"/>
      <arg type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i"/>
      <arg type="u"/>
    </signal>
    <property name="Version" type="u" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
  </interface>
</node>
"""


def build_menu(presets: list[str], active: str | None) -> list[dict]:
    """Return a DBusMenu-style description of the tray menu.

    Presets come first and are radio items, followed by a separator and the
    window and quit commands.
    """
    items: list[dict] = []
    for name in presets:
        items.append(
            {
                "id": f"preset:{name}",
                "label": name,
                "type": "radio",
                "toggle-state": 1 if name == active else 0,
                "enabled": True,
            }
        )
    if items:
        items.append({"type": "separator"})
    items.append({"id": "open", "label": "Open Nitor", "enabled": True})
    items.append({"id": "quit", "label": "Quit", "enabled": True})
    return items


class TrayIcon:
    """A small StatusNotifierItem with a com.canonical.dbusmenu menu.

    ``presets`` and ``active`` may be plain values or callables; callables are
    evaluated every time the menu is laid out, so the tray stays in sync with
    the main window.  ``on_preset``, ``on_open`` and ``on_quit`` are optional
    callbacks invoked from DBus menu events.
    """

    def __init__(
        self,
        *,
        presets: list[str] | Callable[[], list[str]] | None = None,
        active: str | None | Callable[[], str | None] = None,
        on_preset: Callable[[str], None] | None = None,
        on_open: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        title: str = "Nitor",
        icon_name: str = "io.github.hamzaless.Nitor",
    ) -> None:
        self._presets = presets if presets is not None else []
        self._active = active
        self._on_preset = on_preset
        self._on_open = on_open
        self._on_quit = on_quit
        self._title = title
        self._icon_name = icon_name

        self._connection: Any = None
        self._glib: Any = None
        self._registrations: list[int] = []
        self._owned_name: str | None = None
        self._revision = 1
        self._running = False
        self._ids: dict[int, str | None] = {}

    # -- lifecycle -----------------------------------------------------

    def start(self) -> bool:
        """Register the tray icon; return False when that is not possible."""
        if self._running:
            return True
        try:
            from gi.repository import Gio, GLib
        except Exception:
            log.debug("PyGObject is unavailable; tray disabled")
            return False

        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            if connection is None:
                return False
            self._connection = connection
            self._glib = GLib

            sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML)
            menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML)
            self._registrations.append(
                connection.register_object(
                    SNI_PATH,
                    sni_info.interfaces[0],
                    self._on_sni_call,
                    self._on_get_property,
                    self._on_set_property,
                )
            )
            self._registrations.append(
                connection.register_object(
                    MENU_PATH,
                    menu_info.interfaces[0],
                    self._on_menu_call,
                    self._on_get_property,
                    self._on_set_property,
                )
            )

            self._owned_name = self._request_name(connection, Gio, GLib)
            self._register_with_watcher(connection, Gio, GLib)
            self._running = True
            self._emit_layout_updated()
            return True
        except Exception as exc:  # noqa: BLE001 - an optional extra must never crash the app
            log.debug("tray registration failed: %s", exc)
            self.stop()
            return False

    def stop(self) -> None:
        """Unregister everything.  Safe to call more than once."""
        connection = self._connection
        self._connection = None
        self._running = False
        if connection is None:
            return

        with contextlib.suppress(Exception):
            for registration in self._registrations:
                connection.unregister_object(registration)
        self._registrations.clear()

        with contextlib.suppress(Exception):
            from gi.repository import GLib

            if self._owned_name:
                connection.call_sync(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "ReleaseName",
                    GLib.Variant("(s)", (self._owned_name,)),
                    None,
                    0,
                    1000,
                    None,
                )
        self._owned_name = None

        with contextlib.suppress(Exception):
            connection.close_sync(None)

    # -- menu data -----------------------------------------------------

    def _preset_names(self) -> list[str]:
        value = self._presets
        names = value() if callable(value) else value
        return [str(name) for name in (names or [])]

    def _active_name(self) -> str | None:
        value = self._active
        return value() if callable(value) else value

    def _menu_entries(self) -> list[tuple[int, dict]]:
        """Return ``(numeric_id, item)`` pairs for the current menu."""
        self._ids = {}
        entries: list[tuple[int, dict]] = []
        for number, item in enumerate(build_menu(self._preset_names(), self._active_name()), 1):
            self._ids[number] = item.get("id")
            entries.append((number, item))
        return entries

    def _item_properties(self, item: dict) -> dict[str, Any]:
        glib = self._glib
        properties: dict[str, Any] = {"visible": glib.Variant("b", True)}
        if item.get("type") == "separator":
            properties["type"] = glib.Variant("s", "separator")
            return properties
        properties["label"] = glib.Variant("s", str(item.get("label", "")))
        properties["enabled"] = glib.Variant("b", bool(item.get("enabled", True)))
        if item.get("type") == "radio":
            properties["toggle-type"] = glib.Variant("s", "radio")
            properties["toggle-state"] = glib.Variant("i", int(item.get("toggle-state", 0)))
        return properties

    def _layout(self, parent_id: int) -> tuple[int, dict[str, Any], list[Any]]:
        glib = self._glib
        entries = self._menu_entries()
        if parent_id != 0:
            for number, item in entries:
                if number == parent_id:
                    return number, self._item_properties(item), []
            return 0, {"children-display": glib.Variant("s", "submenu")}, []

        children = [
            glib.Variant("(ia{sv}av)", (number, self._item_properties(item), []))
            for number, item in entries
        ]
        return 0, {"children-display": glib.Variant("s", "submenu")}, children

    def _resolve(self, item_id: int) -> str | None:
        return self._ids.get(item_id)

    def _activate(self, item_id: int) -> None:
        # Build the id map on demand: some hosts send Event before they ever
        # call GetLayout (or right after a restart).
        self._menu_entries()
        target = self._resolve(item_id)
        if target is None:
            return
        try:
            if target == "open":
                if self._on_open is not None:
                    self._on_open()
            elif target == "quit":
                if self._on_quit is not None:
                    self._on_quit()
            elif target.startswith("preset:") and self._on_preset is not None:
                self._on_preset(target.removeprefix("preset:"))
        except Exception as exc:  # noqa: BLE001 - callbacks belong to the app
            log.debug("tray callback failed: %s", exc)

    # -- DBus plumbing -------------------------------------------------

    def _request_name(self, connection: Any, Gio: Any, GLib: Any) -> str | None:
        """Own ``org.kde.StatusNotifierItem-<pid>-<n>``; fall back to unique."""
        unique = connection.get_unique_name() or None
        pid = os.getpid()
        for attempt in range(1, 11):
            name = f"{SNI_INTERFACE}-{pid}-{attempt}"
            try:
                reply = connection.call_sync(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "RequestName",
                    GLib.Variant("(su)", (name, 0x4)),  # do not queue
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )
            except Exception:
                return unique
            if reply.unpack()[0] in (1, 4):  # primary owner / already owner
                return name
        return unique

    def _register_with_watcher(self, connection: Any, Gio: Any, GLib: Any) -> None:
        service = self._owned_name or SNI_PATH
        connection.call_sync(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (service,)),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )

    def _emit_layout_updated(self) -> None:
        if self._connection is None:
            return
        self._revision += 1
        with contextlib.suppress(Exception):
            self._connection.emit_signal(
                None,
                MENU_PATH,
                MENU_INTERFACE,
                "LayoutUpdated",
                self._glib.Variant("(ui)", (self._revision, 0)),
            )

    def _on_sni_call(
        self,
        connection: Any,
        sender: str,
        path: str,
        interface: str,
        method: str,
        parameters: Any,
        invocation: Any,
    ) -> None:
        try:
            if method == "Activate" and self._on_open is not None:
                self._on_open()
            invocation.return_value(None)
        except Exception as exc:  # noqa: BLE001 - never leave the caller hanging
            log.debug("tray method %s failed: %s", method, exc)
            with contextlib.suppress(Exception):
                invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(exc))

    def _on_menu_call(
        self,
        connection: Any,
        sender: str,
        path: str,
        interface: str,
        method: str,
        parameters: Any,
        invocation: Any,
    ) -> None:
        glib = self._glib
        try:
            if method == "GetLayout":
                parent_id, _depth, _names = parameters.unpack()
                root = self._layout(int(parent_id))
                invocation.return_value(
                    glib.Variant("(u(ia{sv}av))", (self._revision, root))
                )
            elif method == "GetGroupProperties":
                ids, _names = parameters.unpack()
                entries = self._menu_entries()
                if ids:
                    wanted = set(ids)
                    entries = [(number, item) for number, item in entries if number in wanted]
                invocation.return_value(
                    glib.Variant(
                        "(a(ia{sv}))",
                        (
                            tuple(
                                (number, self._item_properties(item))
                                for number, item in entries
                            ),
                        ),
                    )
                )
            elif method == "GetProperty":
                item_id, property_name = parameters.unpack()
                item = next(
                    (item for number, item in self._menu_entries() if number == item_id),
                    None,
                )
                value = None
                if item is not None:
                    value = self._item_properties(item).get(property_name)
                if value is None:
                    value = glib.Variant("s", "")
                invocation.return_value(glib.Variant("(v)", (value,)))
            elif method == "Event":
                item_id, event_id, _data, _timestamp = parameters.unpack()
                if event_id == "clicked":
                    self._activate(int(item_id))
                invocation.return_value(None)
            elif method == "EventGroup":
                events = parameters.unpack()[0]
                for item_id, event_id, _data, _timestamp in events:
                    if event_id == "clicked":
                        self._activate(int(item_id))
                invocation.return_value(None)
            elif method == "AboutToShow":
                self._emit_layout_updated()
                invocation.return_value(glib.Variant("(b)", (True,)))
            elif method == "AboutToShowGroup":
                self._emit_layout_updated()
                invocation.return_value(glib.Variant("(aiai)", ([], [])))
            else:
                invocation.return_value(None)
        except Exception as exc:  # noqa: BLE001 - never leave the caller hanging
            log.debug("tray menu method %s failed: %s", method, exc)
            with contextlib.suppress(Exception):
                invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(exc))

    def _on_get_property(
        self,
        connection: Any,
        sender: str,
        path: str,
        interface: str,
        property_name: str,
    ) -> Any:
        glib = self._glib
        if interface == SNI_INTERFACE:
            values = {
                "Category": glib.Variant("s", "Hardware"),
                "Id": glib.Variant("s", "nitor"),
                "Title": glib.Variant("s", self._title),
                "Status": glib.Variant("s", "Active"),
                "IconName": glib.Variant("s", self._icon_name),
                "IconPixmap": glib.Variant("a(iiay)", []),
                "OverlayIconName": glib.Variant("s", ""),
                "OverlayIconPixmap": glib.Variant("a(iiay)", []),
                "AttentionIconName": glib.Variant("s", ""),
                "AttentionIconPixmap": glib.Variant("a(iiay)", []),
                "AttentionMovieName": glib.Variant("s", ""),
                "ToolTip": glib.Variant(
                    "(sa(iiay)ss)", ("", [], self._title, "Monitor control")
                ),
                "ItemIsMenu": glib.Variant("b", True),
                "Menu": glib.Variant("o", MENU_PATH),
            }
        elif interface == MENU_INTERFACE:
            values = {
                "Version": glib.Variant("u", 3),
                "Status": glib.Variant("s", "normal"),
                "TextDirection": glib.Variant("s", "ltr"),
                "IconThemePath": glib.Variant("as", []),
            }
        else:
            values = {}
        return values.get(property_name)

    def _on_set_property(
        self,
        connection: Any,
        sender: str,
        path: str,
        interface: str,
        property_name: str,
        value: Any,
    ) -> bool:
        return False
