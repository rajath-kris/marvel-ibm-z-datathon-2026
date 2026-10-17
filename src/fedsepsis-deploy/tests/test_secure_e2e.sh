#!/usr/bin/env bash
# Stretch e2e: the same 3 sites (hospA1, hospA2 = hospital A split by patient; hospB) trained twice, plain FedAvg and
# pairwise-masked secure aggregation; the final models must agree within 1e-9. Also: what the aggregator stores
# for one site in secure mode looks random, and the allowed-fields check still passes.
set -uo pipefail
cd "$(dirname "$0")/.."
EV=$PWD/evidence; mkdir -p "$EV"
OUT=$EV/test_secure_aggregation.txt
{
  echo "# secure aggregation, 3 sites  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo "### unit tests (fedsecure + aggregator SECURE mode)"
  docker run --rm --cpus 1 --memory 512m -v "$PWD":/w:ro --entrypoint /opt/venv/bin/python \
    registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest -B /w/tests/test_fedsecure.py 2>&1 | grep -E "^    |^Ran|^OK|FAIL|Error"
  for mode in plain masked; do
    echo; echo "### 3-site run, $mode"
    sec=0; [ $mode = masked ] && sec=1
    SECURE=$sec RUN_LABEL=secure3-$mode ./run_secure.sh > run/secure_$mode.log 2>&1 || { echo "RUN FAILED"; tail -20 run/secure_$mode.log; exit 1; }
    grep -E "^hospA[12]:|^split:|keeping split|pair seeds|pre-flight: Mem|rounds completed|identical|secure=|results saved" run/secure_$mode.log
    if [ $mode = masked ]; then
      echo "--- what the aggregator stores for hospB's normalisation sums (secure mode): first 4 of 138, as fractions of 2**128"
      python3 - run/secure/state/state.json <<'PY'
import json, math, sys
s = json.load(open(sys.argv[1]))
v = s["stats"]["hospB"]["sum"]
us = sorted(x / 2 ** 128 for x in v)
ks = max(max((i + 1) / len(us) - u, u - i / len(us)) for i, u in enumerate(us))
print("  secure flag in state:", s["secure"], "| stored values are integers:", all(isinstance(x, int) for x in v))
print("  ", [round(x / 2 ** 128, 6) for x in v[:4]], "...")
print(f"   KS distance from uniform over all 138: {ks:.3f} (1% critical {1.63 / math.sqrt(len(us)):.3f})")
PY
      echo "--- hospB's real sums for comparison (local only, computed in hospB's container; never sent in the clear)"
      docker exec sec-hospB /opt/venv/bin/python -c "import numpy as np; z=np.load('/data/site_small.npz', allow_pickle=False); print('  ', [round(float(x),1) for x in z['X'].astype(np.float64).sum(axis=0)[:4]], '...')"
      echo "--- (f) allowed fields, secure run"
      docker logs sec-aggregator 2>&1 | grep 'event=request_keys' | sed -E 's/.*path=([^ ]*) keys=(.*)/\1 \2/' | sort | uniq -c
      python3 - "$(docker logs sec-aggregator 2>&1)" <<'PY'
import re, sys
allowed = {"site_id", "n", "sum", "sumsq", "round", "w", "b"}
seen = set()
for line in sys.argv[1].splitlines():
    m = re.search(r"event=request_keys .* keys=(\S*)", line)
    if m:
        seen |= set(filter(None, m.group(1).split(",")))
print("  union of keys:", sorted(seen), "| ASSERT subset of allowed :", bool(seen) and seen <= allowed)
PY
    fi
  done
  echo; echo "### masked vs plain, same 3 sites, 40 rounds"
  python3 tools/compare_models.py results/secure3-masked/final_model.json results/secure3-plain/final_model.json
  python3 - results/secure3-masked/final_model.json results/secure3-plain/final_model.json <<'PY'
import json, sys
a, b = (json.load(open(p))["model"] for p in sys.argv[1:])
d = max([abs(x - y) for x, y in zip(a["w"], b["w"])] + [abs(a["b"] - b["b"])])
print(f"ASSERT max |masked - plain| <= 1e-9 : {d <= 1e-9}  ({d:.3e})")
PY
  echo; echo "### for reference: 3-site plain vs the 2-site model_global.json (different sites, so NOT expected to match)"
  python3 tools/compare_models.py results/secure3-plain/final_model.json ~/handoff/outputs/demo/model_global.json | grep -E "max .w |b diff|mean diff"
} 2>&1 | tee "$OUT"
