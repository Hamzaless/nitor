#!/usr/bin/env bash
# Install Nitor into the user prefix (~/.local by default).
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$PREFIX/share/nitor"

for command in python3; do
    command -v "$command" >/dev/null || {
        echo "error: $command is required" >&2
        exit 1
    }
done

if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null; then
    echo "error: GTK 4 and libadwaita Python bindings are required" >&2
    echo "       Fedora: sudo dnf install python3-gobject gtk4 libadwaita" >&2
    echo "       Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" >&2
    exit 1
fi

if ! command -v ddcutil >/dev/null; then
    echo "warning: ddcutil was not found; Nitor will not be able to talk to a monitor" >&2
fi

install -d "$APP_DIR" "$PREFIX/bin" "$PREFIX/share/applications" \
    "$PREFIX/share/metainfo" "$PREFIX/share/icons/hicolor/scalable/apps"

rm -rf "$APP_DIR/nitor"
cp -r "$SOURCE_DIR/nitor" "$APP_DIR/nitor"

cat > "$PREFIX/bin/nitor" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m nitor "\$@"
EOF
chmod 755 "$PREFIX/bin/nitor"

sed "s|^Exec=nitor$|Exec=$PREFIX/bin/nitor|" \
    "$SOURCE_DIR/data/io.github.hamzaless.Nitor.desktop" \
    > "$PREFIX/share/applications/io.github.hamzaless.Nitor.desktop"
install -m 644 "$SOURCE_DIR/data/io.github.hamzaless.Nitor.metainfo.xml" \
    "$PREFIX/share/metainfo/"
install -m 644 "$SOURCE_DIR/data/icons/hicolor/scalable/apps/io.github.hamzaless.Nitor.svg" \
    "$PREFIX/share/icons/hicolor/scalable/apps/"

if command -v update-desktop-database >/dev/null; then
    update-desktop-database "$PREFIX/share/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Nitor installed to $PREFIX"
echo "Run it with: nitor"
