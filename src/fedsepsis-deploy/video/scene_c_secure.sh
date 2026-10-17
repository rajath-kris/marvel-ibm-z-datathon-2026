#!/usr/bin/env bash
# Scene c: three sites train together; the aggregator only ever sees scrambled numbers.
source "$(dirname "$0")/common.sh"
MAP=${SPLIT_MAP:-$HOME/handoff/icu_map.json}
export HARDEN=1 MTLS=1                  # same configuration as the live stacks (hardened, mutual TLS)
title "Secure aggregation: 3 hospital sites"
say "hospA1  medical ICU    (hospital A)"
say "hospA2  surgical ICU   (hospital A)"
say "hospB   small hospital (hospital B)"
pause
step "Training together, 40 rounds (plain, then secure)"
SPLIT_MAP=$MAP SECURE=0 RUN_LABEL=video-plain  ./run_secure.sh > run/scene_c_plain.log 2>&1 || { tail -5 run/scene_c_plain.log; exit 1; }
SPLIT_MAP=$MAP SECURE=1 RUN_LABEL=video-secure ./run_secure.sh > run/scene_c_secure.log 2>&1 || { tail -5 run/scene_c_secure.log; exit 1; }
say "done: $(grep -o 'rounds completed: [0-9]* / [0-9]*' run/scene_c_secure.log | tail -1)"
pause
step "What the aggregator received (first 3 values)"
python3 - run/secure/state/secure_seen.json <<'PY'
import json, sys
for site, v in sorted(json.load(open(sys.argv[1])).items()):
    print(f"  {site:7s} " + "  ".join(f"{int(x) / 2**128:.4f}" for x in v["w_first_masked"]))
PY
say "(scrambled: each looks like a random number)"
pause
step "Secure result vs plain result"
python3 - results/video-secure/final_model.json results/video-plain/final_model.json <<'PY'
import json, sys
a, b = (json.load(open(p))["model"] for p in sys.argv[1:])
d = max([abs(x - y) for x, y in zip(a["w"], b["w"])] + [abs(a["b"] - b["b"])])
print(f"  secure = plain, max difference {d:.1e}")
PY
docker rm -f sec-aggregator sec-hospA1 sec-hospA2 sec-hospB >/dev/null 2>&1; docker network rm fedsecure-net >/dev/null 2>&1
echo
