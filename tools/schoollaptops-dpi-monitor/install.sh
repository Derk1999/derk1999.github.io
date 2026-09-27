#!/bin/bash
# Installeert de DPI-meting voor de drie schoollaptops op de Mac Mini:
#   ~/scripts/schoollaptops-dpi/schoollaptops_dpi_sync.py  + config.json
#   ~/Library/LaunchAgents/nl.vankampen.schoollaptopsdpi.plist  (elke 15 minuten)
# Draai vanuit de map met dit bestand:  bash install.sh
# Optioneel vooraf zetten om de vragen over te slaan:
#   UNIFI_API_KEY=...  HA_TOKEN=...  bash install.sh
set -eo pipefail

HOMEDIR="${HOME:-/Users/derkvankampen}"
DEST="$HOMEDIR/scripts/schoollaptops-dpi"
SRC="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOMEDIR/Library/LaunchAgents/nl.vankampen.schoollaptopsdpi.plist"
LABEL="nl.vankampen.schoollaptopsdpi"

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "python3 niet gevonden. Installeer de Xcode Command Line Tools (xcode-select --install) en probeer opnieuw."
  exit 1
fi

mkdir -p "$DEST" "$HOMEDIR/Library/LaunchAgents"
cp "$SRC/schoollaptops_dpi_sync.py" "$DEST/schoollaptops_dpi_sync.py"
chmod 755 "$DEST/schoollaptops_dpi_sync.py"

if [ ! -f "$DEST/config.json" ]; then
  echo "Eerste installatie: configuratie invullen."
  UNIFI_API_KEY="${UNIFI_API_KEY:-}"
  HA_TOKEN="${HA_TOKEN:-}"
  IP_THIJS="${IP_THIJS:-192.168.68.88}"
  IP_JOCHEM="${IP_JOCHEM:-192.168.68.94}"
  IP_SIEB="${IP_SIEB:-192.168.68.91}"
  UCG_URL="${UCG_URL:-https://192.168.68.1}"
  HA_URL="${HA_URL:-http://192.168.68.68:8123}"
  UNIFI_USER="${UNIFI_USER:-}"
  UNIFI_PASS="${UNIFI_PASS:-}"
  if [ -z "$UNIFI_API_KEY" ] && [ -z "$UNIFI_USER" ]; then
    read -r -p "UniFi API-sleutel (Enter = overslaan en inloggen met gebruikersnaam/wachtwoord): " UNIFI_API_KEY
  fi
  if [ -z "$UNIFI_API_KEY" ]; then
    [ -z "$UNIFI_USER" ] && read -r -p "UniFi gebruikersnaam (lokaal admin-account van de UCG): " UNIFI_USER
    [ -z "$UNIFI_PASS" ] && read -r -s -p "UniFi wachtwoord (wordt niet getoond): " UNIFI_PASS && echo
  fi
  [ -z "$HA_TOKEN" ] && read -r -p "Home Assistant long-lived token (profiel > Security): " HA_TOKEN
  if [ -z "$HA_TOKEN" ]; then
    echo "HA-token is nodig. Gestopt."
    exit 1
  fi
  if [ -z "$UNIFI_API_KEY" ] && { [ -z "$UNIFI_USER" ] || [ -z "$UNIFI_PASS" ]; }; then
    echo "Of een UniFi API-sleutel, of gebruikersnaam plus wachtwoord is nodig. Gestopt."
    exit 1
  fi
  UNIFI_API_KEY="$UNIFI_API_KEY" UNIFI_USER="$UNIFI_USER" UNIFI_PASS="$UNIFI_PASS" \
  HA_TOKEN="$HA_TOKEN" UCG_URL="$UCG_URL" HA_URL="$HA_URL" \
  IP_THIJS="$IP_THIJS" IP_JOCHEM="$IP_JOCHEM" IP_SIEB="$IP_SIEB" \
  "$PY" - "$DEST/config.json" <<'PYCFG'
import json, os, sys
cfg = {
    "ucg_url": os.environ["UCG_URL"],
    "site": "default",
    "api_key": os.environ.get("UNIFI_API_KEY", "").strip(),
    "username": os.environ.get("UNIFI_USER", "").strip(),
    "password": os.environ.get("UNIFI_PASS", ""),
    "ha_url": os.environ["HA_URL"],
    "ha_token": os.environ["HA_TOKEN"].strip(),
    "clients": [
        {"name": "Thijs", "ip": os.environ["IP_THIJS"], "prefix": "thijs_laptop"},
        {"name": "Jochem", "ip": os.environ["IP_JOCHEM"], "prefix": "jochem_laptop"},
        {"name": "Sieb", "ip": os.environ["IP_SIEB"], "prefix": "sieb_laptop"},
    ],
}
with open(sys.argv[1], "w") as fh:
    json.dump(cfg, fh, indent=2)
PYCFG
  chmod 600 "$DEST/config.json"
  echo "config.json geschreven naar $DEST/config.json"
else
  echo "Bestaande config.json gevonden, laat ik staan."
fi

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DEST/schoollaptops_dpi_sync.py</string>
  </array>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DEST/launchd.log</string>
  <key>StandardErrorPath</key><string>$DEST/launchd.log</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "launchd-job $LABEL geladen (elke 15 minuten)."

echo
echo "Testrun (haalt het verkeer van vandaag op en zet het in Home Assistant):"
"$PY" "$DEST/schoollaptops_dpi_sync.py" --verbose || true
echo
echo "Klaar. Log: $DEST/sync.log   Laatste UCG-antwoorden: $DEST/last_response_*_laptop.json"
