# Functions shared by run_demo.sh and the tests. Source after config.sh.
# Every wait is a bounded until-loop poll; there are no fixed sleeps.

AGG_NAME=fedsepsis-aggregator
RUN_DIR=$PWD/run
SITES=(hospA hospB)
declare -A HOST_PORT=([hospA]=8101 [hospB]=8102)
declare -A STUB_ROWS=([hospA]=2000 [hospB]=1200)
# MODE=real: one .npz per site (override with REAL_DATA_hospA / REAL_DATA_hospB)
declare -A REAL_FILE=([hospA]=${REAL_DATA_hospA:-$PWD/data/site_big.npz} [hospB]=${REAL_DATA_hospB:-$PWD/data/site_small.npz})

require_token() {
  [ -s "$TOKEN_FILE" ] && [ -s "$HDR_FILE" ] || { echo "no token: run ./setup_token.sh first"; exit 1; }
  if [ "$TLS" = 1 ]; then
    [ -s "$TLS_DIR/aggregator.crt" ] && [ -s "$TLS_DIR/aggregator.key" ] || { echo "TLS=1: run ./setup_tls.sh first"; exit 1; }
    TLS_PIN=$(openssl x509 -in "$TLS_DIR/aggregator.crt" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d : | tr A-F a-f)
  fi
}

agg_scheme() { [ "$TLS" = 1 ] && echo https || echo http; }

build_images() {
  docker build -q -t "$AGG_IMAGE" aggregator >/dev/null
  docker build -q -t "$HOSP_IMAGE" -f hospital/Dockerfile . >/dev/null
  echo "built $AGG_IMAGE and $HOSP_IMAGE"
}

ensure_network() {
  docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
  echo "network $NETWORK ready"
}

remove_containers() {
  docker rm -f "$AGG_NAME" "${SITES[@]}" >/dev/null 2>&1 || true
}

fresh_run_dirs() {
  rm -rf "$RUN_DIR/state" "$RUN_DIR/out"
  mkdir -p "$RUN_DIR/state"
  for s in "${SITES[@]}"; do mkdir -p "$RUN_DIR/out/$s" "$RUN_DIR/data/$s"; done
}

prepare_stub_data() {
  for s in "${SITES[@]}"; do
    [ -s "$RUN_DIR/data/$s/site.json" ] && { echo "$s: keeping existing stub data"; continue; }
    docker run --rm $LIMITS -e STUB_ROWS="${STUB_ROWS[$s]}" -v "$RUN_DIR/data/$s":/data \
      --entrypoint /opt/venv/bin/python "$HOSP_IMAGE" -I /app/make_stub_data.py "$s" /data/site.json
  done
}

start_aggregator() {
  local tls=()
  [ "$TLS" = 1 ] && tls=(-e TLS_CERT=/tls/aggregator.crt -e TLS_KEY=/tls/aggregator.key
                         -v "$TLS_DIR/aggregator.crt":/tls/aggregator.crt:ro -v "$TLS_DIR/aggregator.key":/tls/aggregator.key:ro)
  TOKEN="$(cat "$TOKEN_FILE")" docker run -d --name "$AGG_NAME" $LIMITS \
    --network "$NETWORK" --network-alias aggregator \
    -e TOKEN -e EXPECTED_SITES="$(IFS=,; echo "${SITES[*]}")" -e EXPECTED_ROUNDS="$EXPECTED_ROUNDS" \
    -e STATE_DIR=/state -v "$RUN_DIR/state":/state "${tls[@]}" \
    "$AGG_IMAGE" >/dev/null
  wait_until 60 "aggregator healthy" 'agg_get /health | grep -q "\"ok\": true"'
}

start_hospital() {  # start_hospital SITE
  local s=$1 data_mount lib_mount=() data_path
  if [ "$MODE" = real ]; then
    [ -f "${REAL_FILE[$s]}" ] || { echo "MODE=real but ${REAL_FILE[$s]} is missing"; exit 1; }
    [ -d "$PWD/lib/fedsepsis" ] || { echo "MODE=real but ./lib/fedsepsis is missing"; exit 1; }
    data_mount="${REAL_FILE[$s]}:/data/$(basename "${REAL_FILE[$s]}"):ro"
    data_path="/data/$(basename "${REAL_FILE[$s]}")"
    lib_mount=(-v "$PWD/lib":/app/lib:ro)
  else
    data_mount="$RUN_DIR/data/$s:/data:ro"
    data_path=/data/site.json
  fi
  local tls=()
  [ "$TLS" = 1 ] && tls=(-e TLS_CA=/tls/aggregator.crt -e TLS_PIN_SHA256="${PIN_OVERRIDE:-$TLS_PIN}"
                         -v "$TLS_DIR/aggregator.crt":/tls/aggregator.crt:ro)
  TOKEN="$(cat "$TOKEN_FILE")" docker run -d --name "$s" $LIMITS --network "$NETWORK" \
    -e TOKEN -e SITE_ID="$s" -e AGGREGATOR_URL="$(agg_scheme)://aggregator:8000" -e EXPECTED_ROUNDS="$EXPECTED_ROUNDS" \
    -e DATA_PATH="$data_path" -e MODE="$MODE" -e THRESHOLD="$THRESHOLD" -e OUT_DIR=/out \
    -v "$data_mount" -v "$RUN_DIR/out/$s":/out "${lib_mount[@]}" "${tls[@]}" \
    -p 127.0.0.1:"${HOST_PORT[$s]}":8100 "$HOSP_IMAGE" >/dev/null
  wait_until 60 "$s /health" "curl -sf http://127.0.0.1:${HOST_PORT[$s]}/health >/dev/null"
}

# GET an aggregator path from inside its own container; the token stays inside the container.
# With TLS it verifies the server cert against the same pinned cert the hospitals use.
agg_get() {
  if [ -n "$(docker exec "$AGG_NAME" printenv TLS_CERT 2>/dev/null)" ]; then
    docker exec "$AGG_NAME" sh -c 'curl -s --cacert "$TLS_CERT" --resolve aggregator:8000:127.0.0.1 -H "X-Token: $TOKEN" "https://aggregator:8000$1"' _ "$1"
  else
    docker exec "$AGG_NAME" sh -c 'curl -s -H "X-Token: $TOKEN" "http://127.0.0.1:8000$1"' _ "$1"
  fi
}

wait_until() {  # wait_until TIMEOUT_S DESCRIPTION 'shell condition'
  local t=$1 what=$2 cond=$3
  if timeout "$t" bash -c "$(declare -f agg_get); AGG_NAME=$AGG_NAME; until $cond; do sleep 0.5; done"; then
    echo "ok: $what"
  else
    echo "TIMEOUT after ${t}s waiting for: $what"; return 1
  fi
}

wait_done() {
  wait_until "${1:-600}" "aggregator done=true" 'agg_get /status | grep -q "\"done\": true"' || return 1
  for s in "${SITES[@]}"; do
    wait_until 60 "$s final_model.json" "[ -s '$RUN_DIR/out/$s/final_model.json' ]" || return 1
  done
}

print_summary() {
  echo "=== summary ==="
  python3 - "$RUN_DIR/out" "$(agg_get /status)" "${SITES[@]}" <<'EOF'
import json, sys, os
out, status, sites = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3:]
print(f"rounds completed: {status['round']} / {status['expected_rounds']}   done={status['done']}   sites={status['expected_sites']}")
models = {s: json.load(open(os.path.join(out, s, "final_model.json"))) for s in sites}
m = models[sites[0]]["model"]
print("final global model (from %s/out/final_model.json):" % sites[0])
print("  w = [" + ", ".join(f"{v:.6f}" for v in m["w"]) + "]")
print(f"  b = {m['b']:.6f}")
same = all(models[s]["model"] == m for s in sites)
print("all sites hold the identical final model:", same)
EOF
  echo "=== aggregator log ==="
  docker logs "$AGG_NAME" 2>&1
}
