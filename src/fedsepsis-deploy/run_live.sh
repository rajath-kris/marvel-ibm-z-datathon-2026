#!/usr/bin/env bash
# Start (or restart) BOTH live directions, hardened and with mutual TLS between hospitals and the aggregator:
#   forward: hospA, hospB, fedsepsis-aggregator      (/score etc. on 127.0.0.1:8101 / 8102, plain HTTP, as in DEMO_API.md)
#   reverse: rev-hospB, rev-hospA, rev-fedsepsis-aggregator   (127.0.0.1:8201 / 8202)
# Certificates: ~/.fedsepsis/mtls (created on first use by setup_mtls.sh; never inside the project).
# Usage: ./run_live.sh            both directions        ./run_live.sh forward|reverse   one direction
set -euo pipefail
cd "$(dirname "$0")"
export HARDEN=1 MTLS=1 MODE=real SECRET_DIR=${SECRET_DIR:-$HOME/.fedsepsis}
[ -s "$SECRET_DIR/mtls/ca.crt" ] || ./setup_mtls.sh > run/setup_mtls_live.log
ALLOW="fedsepsis-aggregator,hospA,hospB,rev-fedsepsis-aggregator,rev-hospA,rev-hospB,sec-.*,mtest-.*,hard-.*,fedseed_[0-9]+"
for d in ${1:-forward reverse}; do
  DIRECTION=$d ./setup_real.sh > /dev/null
  DIRECTION=$d ALLOW_JOBS=$ALLOW ./run_demo.sh > "run/live_$d.log" 2>&1 || { echo "FAILED: $d (see run/live_$d.log)"; tail -15 "run/live_$d.log"; exit 1; }
  grep -E "pre-flight: Mem|rounds completed|runtime" "run/live_$d.log" | sed "s/^/$d: /"
done
