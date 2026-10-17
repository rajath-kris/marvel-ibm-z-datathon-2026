#!/usr/bin/env bash
# Record exact JSON responses from a live small-hospital endpoint into demo_fixtures/.
# Usage: tools/capture_fixtures.sh OUT_DIR BASE_URL SUFFIX DEPLOYMENT_LABEL PATIENT_ID... 
# Order matters: retune_borrowed sets the stored "retuned" threshold used by audit_retuned / patient_*_retuned.
set -uo pipefail
out=$1 base=$2 sfx=$3 dep=$4; shift 4
mkdir -p "$out"; log=$out/.capture.log; fail=0
cap() {  # cap FILE METHOD PATH [BODY]
  local ts code; ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [ -n "${4:-}" ]; then code=$(curl -s -m 600 -o "$out/$1" -w "%{http_code}" -X "$2" -H "Content-Type: application/json" -d "$4" "$base$3")
  else code=$(curl -s -m 600 -o "$out/$1" -w "%{http_code}" "$base$3"); fi
  echo "$1|$2 $3${4:+ $4}|$ts|$code|$dep" >> "$log"
  [ "$code" = 200 ] || { echo "FAILED $1: HTTP $code: $(head -c 300 "$out/$1")"; fail=1; }
}
cap "health${sfx}.json"          GET  /health
cap "audit_borrowed${sfx}.json"  POST /audit  '{"model":"borrowed"}'
cap "retune_borrowed${sfx}.json" POST /retune '{"model":"borrowed","budget":25}'
cap "audit_retuned${sfx}.json"   POST /audit  '{"model":"retuned"}'
cap "audit_federated${sfx}.json" POST /audit  '{"model":"federated"}'
n2=${sfx:+_news2$sfx}; n2=${n2:-_news2}
cap "audit${n2}.json"            POST /audit  '{"model":"news2"}'
cap "retune${n2}.json"           POST /retune '{"model":"news2","budget":25}'
for p in "$@"; do
  for m in borrowed federated retuned; do cap "patient_${p}_${m}${sfx}.json" GET "/patient?patient_id=$p&model=$m"; done
  cap "patient_${p}${n2}.json" GET "/patient?patient_id=$p&model=news2"
done
exit $fail
