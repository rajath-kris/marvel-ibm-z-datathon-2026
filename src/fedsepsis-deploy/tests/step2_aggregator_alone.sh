#!/usr/bin/env bash
# Step 2: run the aggregator on its own and exercise every endpoint with curl.
# Also covers test (d) idempotency/409 and the state-restore part of test (g).
# The token is read from a header file outside the project and never printed.
set -uo pipefail
cd "$(dirname "$0")/.."
source ./config.sh

NAME=fedsepsis-agg-step2
PORT=18000
STATE=$PWD/run/step2-state
URL=http://127.0.0.1:$PORT

docker rm -f "$NAME" >/dev/null 2>&1
rm -rf "$STATE" && mkdir -p "$STATE"

start_agg() {
  TOKEN="$(cat "$TOKEN_FILE")" docker run -d --name "$NAME" $LIMITS \
    -e TOKEN -e EXPECTED_SITES=hospA,hospB -e EXPECTED_ROUNDS=2 -e STATE_DIR=/state \
    -v "$STATE":/state -p 127.0.0.1:$PORT:8000 "$AGG_IMAGE" >/dev/null
  timeout 60 bash -c "until curl -sf -H @$HDR_FILE $URL/health >/dev/null; do sleep 0.5; done" \
    || { echo "aggregator did not become healthy"; docker logs "$NAME"; exit 1; }
}

req() {  # req LABEL METHOD PATH [JSON]
  local label=$1 method=$2 path=$3 body=${4:-}
  echo "--- $label"
  echo "\$ curl -X $method $path ${body:+-d '$body'}"
  if [ -n "$body" ]; then
    curl -s -w '  [HTTP %{http_code}]\n' -X "$method" -H @"$HDR_FILE" -H 'Content-Type: application/json' -d "$body" "$URL$path"
  else
    curl -s -w '  [HTTP %{http_code}]\n' -X "$method" -H @"$HDR_FILE" "$URL$path"
  fi
}

start_agg
echo "--- no token (expect 401)"
echo "\$ curl $URL/health   (no X-Token header)"
curl -s -w '  [HTTP %{http_code}]\n' "$URL/health"
echo "--- wrong token (expect 401)"
curl -s -w '  [HTTP %{http_code}]\n' -H 'X-Token: wrong-token-wrong-token' "$URL/health"

req "health" GET /health
req "norm before stats" GET /norm
req "global before stats" GET /global
req "stats hospA" POST /stats '{"site_id":"hospA","n":100,"sum":[100,200],"sumsq":[200,500]}'
req "stats hospA again (duplicate)" POST /stats '{"site_id":"hospA","n":100,"sum":[100,200],"sumsq":[200,500]}'
req "update before norm ready (expect 409)" POST /update '{"site_id":"hospA","round":0,"w":[1,2],"b":0.5,"n":100}'
req "stats unknown site (expect 403)" POST /stats '{"site_id":"hospZ","n":1,"sum":[1,1],"sumsq":[1,1]}'
req "stats wrong dimension (expect 400)" POST /stats '{"site_id":"hospB","n":1,"sum":[1],"sumsq":[1]}'
req "stats hospB" POST /stats '{"site_id":"hospB","n":300,"sum":[900,1200],"sumsq":[3000,5000]}'
echo "    expected mean=[2.5,3.5] std=[sqrt(1.75)=1.3229, sqrt(1.5)=1.2247]"
req "norm after both sites" GET /norm
req "global round 0" GET /global
req "update hospA round 0" POST /update '{"site_id":"hospA","round":0,"w":[1,2],"b":0.5,"n":100}'
req "update hospA round 0 duplicate (expect ok, duplicate)" POST /update '{"site_id":"hospA","round":0,"w":[9,9],"b":9,"n":100}'
req "update hospB wrong round 1 (expect 409)" POST /update '{"site_id":"hospB","round":1,"w":[3,4],"b":1.5,"n":300}'
req "status mid-round" GET /status

echo "=== restart aggregator mid-round (test g) ==="
docker stop "$NAME" >/dev/null && docker rm "$NAME" >/dev/null
echo "state file on host: $(ls -l "$STATE"/state.json | awk '{print $1, $3, $5, $9}')"
start_agg
req "status after restart (expect round 0, sites_updated_this_round=[hospA])" GET /status

req "update hospB round 0" POST /update '{"site_id":"hospB","round":0,"w":[3,4],"b":1.5,"n":300}'
echo "    expected w=[2.5,3.5] b=(0.5*100+1.5*300)/400=1.25, round=1"
req "global round 1" GET /global
req "update hospA round 1" POST /update '{"site_id":"hospA","round":1,"w":[2,2],"b":0,"n":100}'
req "update hospB round 1" POST /update '{"site_id":"hospB","round":1,"w":[2,2],"b":0,"n":300}'
req "global after final round (expect round 2, done true)" GET /global
req "update after done (expect 409)" POST /update '{"site_id":"hospA","round":2,"w":[2,2],"b":0,"n":100}'
req "status final" GET /status
req "unknown path (expect 404)" GET /nope

echo "=== aggregator log (since restart) ==="
docker logs "$NAME" 2>&1
docker rm -f "$NAME" >/dev/null
