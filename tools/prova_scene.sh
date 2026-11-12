#!/bin/bash
# One scene in a short version (about 45 s), to film it on the real light.
#   tools/prova_scene.sh temporale | mare | luogo | sole | sonno
# Ctrl-C stops early; the lamp goes back to what it was showing. A wake-up always wins over the scene.
set -euo pipefail
cd "$(dirname "$0")"
S=${SECONDI:-45}
case "${1:-}" in
  temporale) exec python3 scenes.py ${STRISCIA:+--host "$STRISCIA"} temporale film --seconds "$S" ;;
  mare)      exec python3 scenes.py ${STRISCIA:+--host "$STRISCIA"} temporale mare --seconds "$S" ;;
  luogo)     exec python3 scenes.py ${STRISCIA:+--host "$STRISCIA"} luogo "${LUOGO:-Reykjavik}" --tramonto --seconds "$S" ;;
  sole)      exec python3 scenes.py ${STRISCIA:+--host "$STRISCIA"} sole --replay --seconds "$S" ;;
  sonno)     exec python3 scenes.py ${STRISCIA:+--host "$STRISCIA"} sonno --minuti "$(python3 -c "print($S/60)")" ;;
  *) echo "uso: $0 temporale|mare|luogo|sole|sonno   (SECONDI=60 per allungare, LUOGO=Tokyo per cambiare posto)"; exit 2 ;;
esac
