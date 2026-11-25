#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$(mktemp -d "$(dirname "$HERE")/CH3_PUBLIC_RUN_$(date -u +%Y%m%dT%H%M%SZ)_XXXXXX")"
archive(){ rc=$?; trap - EXIT; printf '%s\n' "$rc" > "$OUT/EXIT_CODE.txt"; (cd "$(dirname "$OUT")" && zip -qr "$(basename "$OUT").zip" "$(basename "$OUT")"); echo "Result: $OUT.zip"; exit "$rc"; }
trap archive EXIT
python3 "$HERE/code/verify_manifest.py" | tee "$OUT/VERIFY.log"
python3 "$HERE/code/replay_gravity.py" --out "$OUT/national" | tee "$OUT/GRAVITY.log"
python3 "$HERE/code/reproduce.py" --out "$OUT/tables" | tee "$OUT/MAIN.log"
python3 "$HERE/code/figures.py" --results "$OUT/tables" --out "$OUT/figures"
python3 "$HERE/experiments/nszu/replay.py" --out "$OUT/nszu" | tee "$OUT/NSZU.log"
python3 "$HERE/experiments/viirs/replay.py" --out "$OUT/viirs" | tee "$OUT/VIIRS.log"
