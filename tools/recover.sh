#!/bin/bash
# Join the strip's setup AP, save its log and state, optionally upload a
# firmware, then always go back to the home Wi-Fi.
# usage: recover.sh <out-dir> [firmware.bin]
# The Mac has no internet while this runs (about a minute).
set -u
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$1"
FW="${2:-}"
AP_IP=192.168.4.1
mkdir -p "$OUT"

secret() { sed -n "s/.*-D$1=[\\\\\"']*\([^\\\\\"']*\).*/\1/p" "$PROJ/secrets.ini" | head -1; }
OTA=$(secret OTA_PASS)
HOME_SSID=$(secret WIFI_SSID)
HOME_PASS=$(secret WIFI_PASS)
[ -n "$OTA" ] && [ -n "$HOME_SSID" ] && [ -n "$HOME_PASS" ] || { echo "secrets not found"; exit 2; }

if [ -n "$FW" ]; then
  [ -f "$FW" ] || { echo "firmware not found: $FW"; exit 2; }
  # Without it the next upload and the setup AP would use a different password.
  grep -qa "$OTA" "$FW" || { echo "REFUSED: firmware built without the OTA password"; exit 4; }
fi

[ -z "${DRY:-}" ] || { echo "dry run: checks passed, network untouched"; exit 0; }

rejoin() {
  for _ in 1 2 3; do
    networksetup -setairportnetwork en0 "$HOME_SSID" "$HOME_PASS" >/dev/null 2>&1
    sleep 4
    ping -c 1 -t 2 192.0.2.1 >/dev/null 2>&1 && { echo "back on home wifi"; return; }
  done
  echo "HOME WIFI NOT REJOINED"
}
trap rejoin EXIT

joined=""
for attempt in 1 2 3 4; do
  msg=$(networksetup -setairportnetwork en0 specchio-setup "$OTA" 2>&1)
  echo "join attempt $attempt: ${msg:-ok}"
  for _ in $(seq 1 10); do
    if curl -s -m 2 "http://$AP_IP/api/state" -o "$OUT/state.json"; then joined=1; break 2; fi
    sleep 1
  done
done
[ -n "$joined" ] || { echo "setup AP not reachable"; exit 3; }
echo "on setup AP"

curl -s -m 10 "http://$AP_IP/api/log" -o "$OUT/log.txt" && echo "log saved"
curl -s -m 5 "http://$AP_IP/api/state" -o "$OUT/state.json" && echo "state saved"

if [ -n "$FW" ]; then
  echo "uploading $(basename "$FW") ($(stat -f %z "$FW") bytes)"
  curl -s -m 180 -u "admin:$OTA" -F "firmware=@$FW" "http://$AP_IP/update" -o "$OUT/update-reply.txt" -w "upload http %{http_code} in %{time_total}s\n"
fi
