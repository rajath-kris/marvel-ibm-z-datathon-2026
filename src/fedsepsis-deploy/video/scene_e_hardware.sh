#!/usr/bin/env bash
# Scene e: the hardware features, measured. LIVE=0 shows the saved results instead of re-running (no containers started).
source "$(dirname "$0")/common.sh"
LIVE=${LIVE:-1}
EV=evidence-hardening
title "LinuxONE hardware features, measured"
step "On-chip crypto (CPACF): our mutual-TLS settings, hardware on vs masked"
if [ "$LIVE" = 1 ]; then ./tests/cpacf_bench.sh | grep -E '^(##|negotiated|mTLS|SHAKE)'; else grep -E '^(##|negotiated|mTLS|SHAKE)' $EV/cpacf_tls_bench.txt; fi
say "Honest limit: this shows the TLS stack we use is faster with CPACF, not that a federated round used it."
pause
step "AI accelerator (NNPA): optional scoring vs the CPU path, real demo data"
if [ "$LIVE" = 1 ]; then
  docker run --rm --user 1000:1000 --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=128m -e HOME=/tmp --cap-drop ALL \
    --security-opt no-new-privileges:true --pids-limit 256 --memory 3g --memory-swap 3g --cpus 2 \
    -v "$PWD/lib":/app/lib:ro -v "$HOME/handoff/outputs/demo":/data:ro -v "$PWD/tests":/t:ro \
    --entrypoint /opt/venv/bin/python fedsepsis-hospital:nnpa -I /t/test_nnpa_parity.py | grep -E '^(install|scores|alert rate|time|PARITY|per-patient|RESULT)'
else
  grep -E '^(install|scores|alert rate|time|PARITY|per-patient|RESULT)' $EV/nnpa_parity_forward.txt
fi
say "It runs on the accelerator and matches the CPU within our limit. It is off by default and not faster for this small model."
echo
