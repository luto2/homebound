#!/bin/sh
# Install, update or remove Homebound for the current user.
#
#   curl -fsSL https://raw.githubusercontent.com/luto2/homebound/master/packaging/linux/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/luto2/homebound/master/packaging/linux/install.sh | sh -s -- --uninstall
#
# Installs the libraries the AppImage needs (asks for sudo), downloads the latest release to
# ~/.local/bin/homebound and adds Homebound to the app menu. Running it again updates. Maps in
# ~/.local/share/homebound/ are never touched.
set -eu

REPO="luto2/homebound"
RAW="https://raw.githubusercontent.com/$REPO/master"
BIN="$HOME/.local/bin/homebound"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
DESKTOP="$APPS/homebound.desktop"

say() { printf '%s\n' "$*"; }
fail() { printf 'Homebound install: %s\n' "$*" >&2; exit 1; }

refresh_menu() {
    # Plasma keeps its own menu and icon caches; without a refresh the icon can stay missing.
    if command -v kbuildsycoca6 >/dev/null 2>&1; then kbuildsycoca6 >/dev/null 2>&1 || true; fi
    if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database "$APPS" >/dev/null 2>&1 || true; fi
}

uninstall() {
    rm -f "$BIN" "$DESKTOP" "$ICONS/256x256/apps/homebound.png" "$ICONS/48x48/apps/homebound.png" \
        "$ICONS/scalable/apps/homebound.svg"
    refresh_menu
    say "Homebound removed. Your maps stay in ~/.local/share/homebound/."
}

install_libraries() {
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y curl fuse3 libxcb-cursor0
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y curl fuse3 xcb-util-cursor
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm curl fuse3 xcb-util-cursor
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y curl fuse3 libxcb-cursor0
    else
        say "Unknown package manager: install FUSE 3 and the xcb-cursor library yourself if Homebound doesn't start."
    fi
}

install() {
    command -v curl >/dev/null 2>&1 || fail "curl is needed to download Homebound."
    install_libraries
    mkdir -p "$(dirname "$BIN")" "$APPS" "$ICONS/256x256/apps" "$ICONS/48x48/apps" "$ICONS/scalable/apps"

    url=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | grep -o 'https://[^"]*x86_64\.AppImage' | head -n1) || true
    [ -n "${url:-}" ] || fail "couldn't find the latest release on GitHub."
    # Download next to the target and move it in, so a running Homebound or a broken download
    # never leaves a half-written file behind.
    curl -fL -o "$BIN.download" "$url"
    chmod +x "$BIN.download"
    mv -f "$BIN.download" "$BIN"

    curl -fsSL -o "$ICONS/256x256/apps/homebound.png" "$RAW/packaging/icons/homebound.png"
    curl -fsSL -o "$ICONS/48x48/apps/homebound.png" "$RAW/packaging/icons/homebound-48.png"
    curl -fsSL -o "$ICONS/scalable/apps/homebound.svg" "$RAW/packaging/icons/homebound.svg"
    curl -fsSL "$RAW/packaging/linux/homebound.desktop" | sed "s|^Exec=.*|Exec=$BIN|" > "$DESKTOP"
    refresh_menu
    say "Homebound installed. Start it from the app menu or with ~/.local/bin/homebound"
}

case "${1:-}" in
    "") install ;;
    --uninstall) uninstall ;;
    *) fail "unknown option $1 (use --uninstall or nothing)" ;;
esac
