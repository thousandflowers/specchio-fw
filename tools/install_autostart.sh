#!/bin/bash
# Start the Home helper (scenes.py casa) with the Mac, or stop doing so.
#   install_autostart.sh            install, or update after changing the tools
#   install_autostart.sh remove     stop it and remove it
#
# Two macOS rules shape this:
# - a program started at login may not read Desktop or Documents without a click on a privacy prompt,
#   so the tools run from a copy in ~/Library/Application Support/specchio;
# - a Python started directly by launchd is kept off the home network (Local Network privacy),
#   the same Python started through osascript is let through.
# The live settings (local.json, purifier.json) are the ones in the copy: an update never overwrites them.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/specchio/tools"
LABEL=com.specchio-fw.casa
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/specchio-casa.log"
PYTHON="$(command -v python3)"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = remove ]; then
  rm -f "$PLIST"
  echo "avvio automatico tolto (la copia in $DEST resta, con le tue impostazioni)"
  exit 0
fi

mkdir -p "$DEST"
rsync -a --delete --exclude __pycache__ --exclude local.json --exclude purifier.json --exclude '*.tmp' "$SRC/" "$DEST/"
for kept in local.json purifier.json; do
  if [ ! -f "$DEST/$kept" ] && [ -f "$SRC/$kept" ]; then cp -p "$SRC/$kept" "$DEST/$kept"; fi
done

xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
COMMAND="cd '$DEST' && PATH=$(dirname "$PYTHON"):/usr/bin:/bin:/usr/sbin:/sbin PYTHONUNBUFFERED=1 '$PYTHON' scenes.py casa >> '$LOG' 2>&1"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/osascript</string><string>-e</string>
    <string>$(printf 'do shell script "%s"' "$(printf '%s' "$COMMAND" | sed 's/["\\]/\\&/g')" | xml)</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$(printf '%s' "$LOG" | xml)</string>
  <key>StandardErrorPath</key><string>$(printf '%s' "$LOG" | xml)</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "avvio automatico installato: parte ora e a ogni accesso al Mac"
echo "registro: $LOG"
echo "togliere: $0 remove"
