#!/usr/bin/env bash
# Wrap the PyInstaller folder (dist/homebound) into dist/Homebound-<version>-x86_64.AppImage.
# Run after the PyInstaller build. Uses $APPIMAGETOOL, else downloads appimagetool into build/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
APPDIR=build/AppDir
TOOL="${APPIMAGETOOL:-build/appimagetool-x86_64.AppImage}"

[ -x dist/homebound/homebound ] || { echo "dist/homebound is missing: run PyInstaller first" >&2; exit 1; }
VERSION="$(dist/homebound/homebound --version | awk '{print $2}')"
if [ ! -x "$TOOL" ]; then
    mkdir -p build
    curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a dist/homebound "$APPDIR/usr/lib/homebound"
cp packaging/linux/AppRun "$APPDIR/AppRun"
cp packaging/linux/homebound.desktop "$APPDIR/homebound.desktop"
cp packaging/linux/homebound.desktop "$APPDIR/usr/share/applications/"
cp packaging/icons/homebound.png "$APPDIR/homebound.png"
cp packaging/icons/homebound.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
cp packaging/icons/homebound.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
ln -s homebound.png "$APPDIR/.DirIcon"

# CI runners have no FUSE; extract-and-run works everywhere
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$TOOL" --no-appstream "$APPDIR" "dist/Homebound-$VERSION-x86_64.AppImage"
