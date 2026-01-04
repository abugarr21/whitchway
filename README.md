# whitchway
Observe-only runtime probe for capturing factual system and application state.
this is the command I specifically use for running Whitchway scans. I run the tooling from a flash drive and write results to an adjacent folder with indicators.
sudo bash -lc '
set -euo pipefail

BASE="/media/abugarr/PU-Key/section8.4"
RESULTS_ROOT="${BASE}/results"
HOST="$(hostname -s 2>/dev/null || hostname)"
TS_MS="$(date -u +%s%3N)"
OUTDIR="${RESULTS_ROOT}/${HOST}/${TS_MS}/whitchway"

mkdir -p "$OUTDIR"
case "$OUTDIR" in
  /media/*/PU-Key/section8.4/results/*) ;;
  *) echo "FATAL: OUTDIR not under expected PU-Key results: $OUTDIR"; exit 12;;
esac

cd "$OUTDIR"

PROBE="${BASE}/whitchway_probe.py"
WHITCH="${BASE}/section8_4_whitchway.py"

# If hbai exists on this host, route capture is a nice demo.
APP_SPEC="hbai.app.main:app"
ROOT="/opt/hbai"

# Probe: captures factual state surfaces (system + optional app routes)
if [[ -d "$ROOT" ]]; then
  export PYTHONPATH="$ROOT"
  python3 "$PROBE" --root "$ROOT" --app "$APP_SPEC" --out "whitchway_probe.jsonl" |& tee probe_console.txt || true
  python3 "$WHITCH" --root "$ROOT" --jsonl --runtime-app "$APP_SPEC" |& tee whitchway_console.txt || true
else
  python3 "$PROBE" --root "$BASE" --out "whitchway_probe.jsonl" |& tee probe_console.txt || true
  python3 "$WHITCH" --root "$BASE" --jsonl |& tee whitchway_console.txt || true
fi

chown -R abugarr:abugarr "$(dirname "$OUTDIR")" || true
chmod -R u+rwX,go+rX "$(dirname "$OUTDIR")" || true

echo
echo "✔ DONE: $OUTDIR"
ls -lah "$OUTDIR" | sed -n "1,200p"
'
