#!/bin/bash
# Upload a firmware over the home network and say how it ended:
#   JOINED       the new firmware answers
#   ROLLED BACK  it did not join and the guard put the known-good one back
#   LOST         nothing answers: recover through the setup AP (tools/recover.sh)
# usage: try_fw.sh [firmware.bin]      default: the last build
# Only for guarded builds (-DSELF_RESTORE): it refuses to upload unless the
# strip already holds a valid known-good image to fall back to.
set -u
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
FW="${1:-$PROJ/.pio/build/specchio/firmware.bin}"
HOST="${HOST:-192.0.2.66}"
SKETCH_LIMIT=$((0x98000))   # above this the upload would overwrite the fallback image (see src/guard.cpp)
WAIT_S=150                  # guard grace 60 s + install + boot, with margin

OTA=$(sed -n "s/.*-DOTA_PASS=[\\\\\"']*\([^\\\\\"']*\).*/\1/p" "$PROJ/secrets.ini" | head -1)
[ -n "$OTA" ] || { echo "OTA password not found in secrets.ini"; exit 2; }
[ -f "$FW" ] || { echo "firmware not found: $FW"; exit 2; }
grep -qa "$OTA" "$FW" || { echo "REFUSED: firmware built without the OTA password"; exit 4; }
[ "$(stat -f %z "$FW")" -lt "$SKETCH_LIMIT" ] || { echo "REFUSED: firmware too big for the guarded layout"; exit 4; }

fw_now() { curl -s -m 3 "http://$HOST/api/state" | sed -n 's/.*"fw":"\([^"]*\)".*/\1/p'; }
before=$(fw_now)
[ -n "$before" ] || { echo "strip not reachable at $HOST"; exit 3; }
if [ -z "${NO_STASH_CHECK:-}" ]; then  # a plain (unguarded) firmware has no /api/stash: the image is still in flash
  stash=$(curl -s -m 10 "http://$HOST/api/stash")
  case "$stash" in *is_known_good=1*) ;; *) echo "REFUSED: no valid fallback image on the strip ($stash)"; exit 4;; esac
fi

echo "running $before, uploading $(basename "$FW") ($(stat -f %z "$FW") bytes)"
curl -s -m 120 -u "admin:$OTA" -F "firmware=@$FW" "http://$HOST/update" -o /dev/null -w "upload http %{http_code} in %{time_total}s\n"

started=$(date +%s)
sleep 8  # the reboot: an answer before this is still the old firmware
while [ $(( $(date +%s) - started )) -lt "$WAIT_S" ]; do
  now=$(fw_now)
  if [ -n "$now" ]; then
    took=$(( $(date +%s) - started ))
    if strings "$FW" | grep -qx "$now"; then echo "JOINED: $now after ${took}s"; exit 0; fi
    echo "ROLLED BACK: $now answers after ${took}s, the upload did not join"; exit 1
  fi
  sleep 3
done
echo "LOST: nothing answers after ${WAIT_S}s"; exit 5
