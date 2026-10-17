#!/usr/bin/env bash
# MODE=real checks for /audit, /retune, /patient, /score against HANDOFF.md section 6 and demo_patient.json,
# plus the allowed-fields check on the real run. Run after `MODE=real ./run_demo.sh` (demo left running).
# NOTE: /retune changes the stored "retuned" threshold of each hospital (that is its job).
set -uo pipefail
cd "$(dirname "$0")/.."
source ./config.sh
source ./demo_lib.sh
EV=$PWD/evidence; mkdir -p "$EV"
OUT=$EV/test_real_endpoints.txt
B=http://127.0.0.1:8102; A=http://127.0.0.1:8101

req() {  # req LABEL METHOD URL [JSON]
  echo "--- $1"; echo "\$ curl -X $2 ${3#http://127.0.0.1} ${4:+-d '$4'}"
  if [ -n "${4:-}" ]; then curl -s -m 600 -w ' [HTTP %{http_code}]\n' -X "$2" -d "$4" "$3"
  else curl -s -m 600 -w ' [HTTP %{http_code}]\n' "$3"; fi
}
# full replay JSON is long: print a summary, keep the raw response for the comparison below
replay() {  # replay PID MODEL
  curl -s -m 120 "$B/patient?patient_id=$1&model=$2" > "run/replay_$1_$2.json"
  python3 - "run/replay_$1_$2.json" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
print(f"  patient {r['patient_id']} model={r['model']} threshold={r['threshold']:.6f} data={r['data']} hours 0..{r['hours'][-1]} "
      f"onset={r['onset_hour']} window={r['window']} alert_hours={r['alert_hours']} max_score={max(r['score']):.4f}")
EOF
}

{
  echo "# MODE=real endpoint checks  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  req "hospB health" GET "$B/health"
  echo; echo "=== input validation"
  req "audit: unknown model (expect 400)" POST "$B/audit" '{"model":"bogus"}'
  req "audit: threshold out of range (expect 400)" POST "$B/audit" '{"model":"borrowed","threshold":2}'
  req "retune: bad budget (expect 400)" POST "$B/retune" '{"model":"borrowed","budget":-5}'
  req "retune: model=retuned is not allowed (expect 400)" POST "$B/retune" '{"model":"retuned","budget":25}'
  echo; echo "=== /audit at hospB on small_test_patients.npz (all rows; HANDOFF section 6)"
  req "audit borrowed, stored threshold (experiment setup 1 @25: 11.67)" POST "$B/audit" '{}'
  req "audit borrowed at the setup-2 threshold 0.425315265515 (experiment: 23.995858)" POST "$B/audit" '{"model":"borrowed","threshold":0.425315265515}'
  req "audit federated (this run's model), stored threshold 0.428312 (experiment setup 4 @25 with model_global: 22.453905)" POST "$B/audit" '{"model":"federated"}'
  echo; echo "=== /retune at hospB (data: $(curl -s $B/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["retune_data"])'))"
  req "retune borrowed to budget 25 (experiment setup 2 on small_val_patients: 0.425315265515)" POST "$B/retune" '{"model":"borrowed","budget":25}'
  req "audit retuned borrowed on the test patients (experiment setup 2 @25: 23.995858)" POST "$B/audit" '{"model":"retuned"}'
  req "retune federated to budget 25 (experiment setup 4: 0.428311566 sweep / 0.42831155832 float64 run_repro)" POST "$B/retune" '{"model":"federated","budget":25}'
  req "audit retuned federated on the test patients (experiment setup 4 @25: 22.453905)" POST "$B/audit" '{"model":"retuned"}'
  echo; echo "=== replay: demo_patient.json examples (any test patient works)"
  for p in 71 13561; do for m in federated borrowed; do replay $p $m; done; done
  req "replay unknown patient (expect 404)" GET "$B/patient?patient_id=999999&model=federated"
  echo; echo "=== /score at hospB"
  for h in 19 28 29 34; do req "score patient 71 hour $h, federated" POST "$B/score" "{\"patient_id\":71,\"hour\":$h,\"model\":\"federated\"}"; done
  req "score patient 71 hour 29, borrowed" POST "$B/score" '{"patient_id":71,"hour":29,"model":"borrowed"}'
  echo; echo "=== replay vs demo_patient.json (reference: model_federated_best.json / model_big_asis.json, same thresholds)"
  python3 - "$REAL_DIR/demo_patient.json" <<'EOF'
import json, sys
dp = json.load(open(sys.argv[1]))
for kind in ("success_example", "failure_example"):
    ex = dp[kind]
    for m, ref in (("federated", "federated"), ("borrowed", "asis")):
        r = json.load(open(f"run/replay_{ex['patient_id']}_{m}.json"))
        n = len(ex["hours"])          # the example covers hours 0..onset+3
        d = max(abs(a - b) for a, b in zip(r["score"][:n], ex["score_" + ref]))
        same = r["alert"][:n] == ex["alert_" + ref]
        print(f"  {kind:16s} patient {ex['patient_id']:>5} {m:9s}: max |score diff| {d:.2e}, alerts identical: {same}, "
              f"alert hours (ours) {[h for h, a in zip(r['hours'][:n], r['alert'][:n]) if a]}")
EOF
  echo; echo "=== hospA (big site) audit on its own training rows, borrowed model + its own threshold"
  req "hospA health" GET "$A/health"
  req "hospA audit borrowed" POST "$A/audit" '{}'
  echo; echo "=== CORS (demo page on http://localhost)"
  echo "\$ curl -i -X OPTIONS -H 'Origin: http://localhost:5173' -H 'Access-Control-Request-Method: POST' /audit"
  curl -s -i -X OPTIONS -H 'Origin: http://localhost:5173' -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: content-type' "$B/audit" | tr -d '\r' | grep -E '^HTTP|^Access-Control'
  echo "\$ curl -i -H 'Origin: http://localhost:5173' /health"
  curl -s -i -H 'Origin: http://localhost:5173' "$B/health" | tr -d '\r' | grep -E '^HTTP|^Access-Control-Allow-Origin'
  echo "\$ curl -i -H 'Origin: http://evil.example' /health   (expect no Access-Control-Allow-Origin)"
  curl -s -i -H 'Origin: http://evil.example' "$B/health" | tr -d '\r' | grep -E '^HTTP|^Access-Control-Allow-Origin' || true
  echo; echo "=== (f) allowed fields on the real run"
  docker logs "$AGG_NAME" 2>&1 | grep 'event=request_keys' | sed -E 's/.*path=([^ ]*) keys=(.*)/\1 \2/' | sort | uniq -c
  python3 - "$(docker logs "$AGG_NAME" 2>&1)" <<'EOF'
import re, sys
allowed = {"site_id", "n", "sum", "sumsq", "round", "w", "b"}
seen = set()
for line in sys.argv[1].splitlines():
    m = re.search(r"event=request_keys .* keys=(\S*)", line)
    if m:
        seen |= set(filter(None, m.group(1).split(",")))
print("union of keys:", sorted(seen), "| ASSERT subset of allowed :", bool(seen) and seen <= allowed)
EOF
} 2>&1 | tee "$OUT"
