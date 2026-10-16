#!/bin/bash
# Rebuild the native LCARS Omnigent app into /Applications.
# Icon: app/favicon.png is converted to AppIcon.icns (iconutil) if missing.
set -e
cd "$(dirname "$0")/.."
APP="/Applications/LCARS Omnigent.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -o "$APP/Contents/MacOS/LCARS Omnigent" macapp/main.swift
if [ ! -f "$APP/Contents/Resources/AppIcon.icns" ]; then
  echo "note: no AppIcon.icns found; generate one from app/favicon.png (iconutil) or copy an existing icns"
fi
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>LCARS Omnigent</string>
  <key>CFBundleDisplayName</key>       <string>LCARS Omnigent</string>
  <key>CFBundleIdentifier</key>        <string>local.lcars.omnigent</string>
  <key>CFBundleVersion</key>           <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key>        <string>LCARS Omnigent</string>
  <key>CFBundleIconFile</key>          <string>AppIcon</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>LSMinimumSystemVersion</key>    <string>11.0</string>
  <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLIST
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
echo "built $APP"
