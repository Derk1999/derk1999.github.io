#!/bin/bash
set -eo pipefail

BASE_URL="https://raw.githubusercontent.com/derk1999/derk1999.github.io/claude/chrome-boek-school-website-femtjq/toezicht-websites"
DIR="$HOME/scripts/toezicht"
PLIST="$HOME/Library/LaunchAgents/nl.vankampen.toezicht.plist"

echo "== Toezicht websites-pipeline installeren =="
mkdir -p "$DIR"

curl -fsSL "$BASE_URL/sync_toezicht_websites.py" -o "$DIR/sync_toezicht_websites.py"
chmod +x "$DIR/sync_toezicht_websites.py"

if [ ! -f "$DIR/config.json" ]; then
  curl -fsSL "$BASE_URL/config.json.example" -o "$DIR/config.json"
  chmod 600 "$DIR/config.json"
  echo "config.json aangemaakt - vul zo je HA-login in."
else
  echo "config.json bestaat al - blijft ongemoeid."
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>nl.vankampen.toezicht</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$DIR/sync_toezicht_websites.py</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>$DIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$DIR/launchd.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo ""
echo "Klaar. Volgende stappen:"
echo "1. open -e $DIR/config.json   en vul username/password in"
echo "2. python3 $DIR/sync_toezicht_websites.py   als test"
echo "3. check het Toezicht-dashboard in Home Assistant"
