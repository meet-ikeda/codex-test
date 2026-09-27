#!/bin/sh
# Builds ~/Applications/exobrain.app: opens the exobrain screen in its own Chrome app window
# (a plain browser tab if Chrome is not installed), waking the resident screen first if it is down.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/exobrain.app"
mkdir -p "$HOME/Applications"
cat > /tmp/exobrain-app.applescript <<'SCRIPT'
set theURL to "http://127.0.0.1:8765/"
try
	do shell script "curl -s -o /dev/null --max-time 2 " & theURL
on error
	do shell script "launchctl kickstart gui/$(id -u)/jp.exobrain.app || $HOME/.local/bin/exobrain open >/dev/null 2>&1 &"
	delay 2
end try
try
	do shell script "open -na 'Google Chrome' --args --app=" & theURL
on error
	open location theURL
end try
SCRIPT
rm -rf "$APP"
osacompile -o "$APP" /tmp/exobrain-app.applescript
cp "$HERE/icon/exobrain.icns" "$APP/Contents/Resources/applet.icns"
# The default icon in Assets.car would win over the .icns: use only ours.
rm -f "$APP/Contents/Resources/Assets.car"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleName exobrain" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string jp.exobrain.launcher" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier jp.exobrain.launcher" "$APP/Contents/Info.plist"
codesign --force --sign - "$APP" >/dev/null 2>&1 || true  # re-seal after editing (ad hoc, this Mac only)
touch "$APP"
echo "$APP"
