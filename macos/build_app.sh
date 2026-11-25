#!/bin/bash
# Builds "Flow Bench.app", a double-clickable app with an icon. The program code is copied INTO the app
# (Contents/Resources/app): macOS does not let an app read scripts from ~/Documents, ~/Desktop or
# ~/Downloads without a privacy grant, so an app that pointed back at this folder would silently fail
# to start. Rebuild the app after changing the code.
#   macos/build_app.sh [destination-folder]      (default: this tool's folder)
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HERE}"
APP="$DEST/Flow Bench.app"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/flowbench/__init__.py")"
rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
cp "$HERE/launch.sh" "$HERE/requirements.txt" "$HERE/LICENSE" "$APP/Contents/Resources/app/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' "$HERE/flowbench" "$APP/Contents/Resources/app/"
cat > "$APP/Contents/MacOS/FlowBench" <<'X'
#!/bin/bash
export MODE=gui
exec /bin/bash "$(cd "$(dirname "$0")/../Resources/app" && pwd)/launch.sh"
X
chmod +x "$APP/Contents/MacOS/FlowBench"
cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Flow Bench</string>
  <key>CFBundleDisplayName</key><string>Flow Bench</string>
  <key>CFBundleIdentifier</key><string>io.github.pxkimx.flow-bench</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>FlowBench</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Copyright (c) 2026 Paul H. Kim. MIT licence.</string>
</dict></plist>
PL
# the icon needs Pillow: use the app's own environment if it exists, else any python3 that has it
PY="$HOME/Library/Application Support/Flow Bench/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
"$PY" "$HERE/macos/make_icon.py" "$APP/Contents/Resources/AppIcon.icns"
touch "$APP"
echo "Built: $APP ($VERSION, $(du -sh "$APP" | cut -f1))"
