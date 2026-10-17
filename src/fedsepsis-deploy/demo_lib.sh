# Functions shared by run_demo.sh and the tests. Source after config.sh.
# Every wait is a bounded until-loop poll; there are no fixed sleeps.

# Overridable so a second deployment (run_secure.sh) can run beside the main one with its own names.
NAME_PREFIX=${NAME_PREFIX:-$DIR_PREFIX}      # container name = $NAME_PREFIX$site
AGG_NAME=${AGG_NAME:-${NAME_PREFIX}fedsepsis-aggregator}
RUN_DIR=${RUN_DIR:-$PWD/$DIR_RUN}
read -r -a SITES <<< "${FED_SITES:-$BIG_SITE $SMALL_SITE}"
if [ "$DIRECTION" = reverse ]; then
  declare -A HOST_PORT=([hospB]=8201 [hospA]=8202)
else
  declare -A HOST_PORT=([hospA]=8101 [hospB]=8102 [hospA1]=8111 [hospA2]=8112)
fi
declare -A STUB_ROWS=([hospA]=2000 [hospB]=1200)
declare -A HOSP_MEMORY=([$BIG_SITE]=2g [$SMALL_SITE]=1g [hospA1]=1g [hospA2]=1g)
SECURE=${SECURE:-0}
PAIR_DIR=${PAIR_DIR:-$SECRET_DIR/pairs}      # pairwise seeds for SECURE=1 (outside the project)
# MODE=real: training file per site (override with REAL_DATA_hospA / REAL_DATA_hospB)
declare -A REAL_FILE=([$BIG_SITE]=$REAL_DIR/site_big.npz [$SMALL_SITE]=$REAL_DIR/site_small.npz
                      [hospA1]=$RUN_DIR/split/hospA1.npz [hospA2]=$RUN_DIR/split/hospA2.npz)
# /audit, /score, /patient data per site (default: its training file). hospB: held-out test patients, all rows
# (HANDOFF.md section 3/5: alert metrics use all rows; keep_row is only for AUROC and is not used here).
declare -A EVAL_FILE=([$SMALL_SITE]=$REAL_DIR/small_test_patients.npz)
# /retune data per site (default: its training file). hospB: small_val_patients.npz, the set the experiment
# re-tuned on (HANDOFF.md section 6), so the demo reproduces setups 2 and 4. Decided 2026-10-17.
declare -A RETUNE_FILE=([$SMALL_SITE]=${RETUNE_DATA_SMALL:-$REAL_DIR/small_val_patients.npz})
# NEWS2 (partial) scores: one per row of the small site's test file; thresholds documented per budget
declare -A NEWS2_FILE=([$SMALL_SITE]=$REAL_DIR/news2_scores.npz)
BORROWED_MODEL=$REAL_DIR/model_big_asis.json
DEMO_PATIENT=$REAL_DIR/demo_patient.json
REPRO_CONFIG=$REAL_DIR/repro_config.json
TRAIN_ENV=()
INIT_BIAS=0.0

# Hyper-parameters for MODE=real come from repro_config.json (setup4 = FedAvg big + small), not from here.
load_train_config() {
  if [ "$MODE" != real ]; then
    EXPECTED_ROUNDS=${EXPECTED_ROUNDS:-5}; INIT_BIAS=0.0; TRAIN_ENV=(); return
  fi
  [ -s "$REPRO_CONFIG" ] || { echo "MODE=real but $REPRO_CONFIG is missing"; exit 1; }
  eval "$(python3 - "$REPRO_CONFIG" <<'EOF'
import json, sys
c = json.load(open(sys.argv[1])); s4 = c["setup4"]
print(f"CFG_ROUNDS={int(s4['rounds'])} CFG_LR={float(s4['lr'])!r} CFG_EPOCHS={int(s4['local_epochs'])} "
      f"CFG_L2={float(c['l2'])!r} CFG_CW={float(c['class_weight'])!r} CFG_B0={float(c['b0'])!r}")
EOF
)"
  EXPECTED_ROUNDS=${EXPECTED_ROUNDS:-$CFG_ROUNDS}
  INIT_BIAS=$CFG_B0
  TRAIN_ENV=(-e LR="$CFG_LR" -e EPOCHS="$CFG_EPOCHS" -e L2="$CFG_L2" -e CLASS_WEIGHT="$CFG_CW")
  echo "train config (repro_config.json setup4): rounds=$EXPECTED_ROUNDS lr=$CFG_LR local_epochs=$CFG_EPOCHS l2=$CFG_L2 class_weight=$CFG_CW b0=$CFG_B0"
}

# Shared VM: refuse to start if memory is short or someone else's heavy job is running.
preflight() {
  local avail others busy
  avail=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
  others=$(docker ps --format '{{.Names}}' | grep -v -x -E "notebook|$AGG_NAME|$(container_names | paste -sd'|' -)" | paste -sd, - || true)
  busy=$(ps -eo pcpu=,user=,comm= --sort=-pcpu | awk '$1 > 50' | tr '\n' ';' || true)
  echo "pre-flight: MemAvailable ${avail} MB (need >= $MIN_AVAILABLE_MB); other containers: ${others:-none}; processes >50% CPU: ${busy:-none}"
  # ALLOW_JOBS=name1,name2: containers the team agreed may run alongside (CPU check is then skipped; memory is not)
  local unexpected=$others
  if [ -n "${ALLOW_JOBS:-}" ]; then
    unexpected=$(echo "$others" | tr , "\n" | grep -v -x -E "$(echo "$ALLOW_JOBS" | tr , '|')" || true)
    echo "pre-flight: allowed to run alongside: $ALLOW_JOBS"
    busy=""
  fi
  if [ -n "$unexpected" ] || [ -n "$busy" ]; then echo "STOP: another job is running on the VM"; exit 3; fi
  if [ "$avail" -lt "$MIN_AVAILABLE_MB" ]; then echo "STOP: less than $MIN_AVAILABLE_MB MB available"; exit 3; fi
}

require_token() {
  [ -s "$TOKEN_FILE" ] && [ -s "$HDR_FILE" ] || { echo "no token: run ./setup_token.sh first"; exit 1; }
  if [ "$TLS" = 1 ]; then
    [ -s "$TLS_DIR/aggregator.crt" ] && [ -s "$TLS_DIR/aggregator.key" ] || { echo "TLS=1: run ./setup_tls.sh first"; exit 1; }
    TLS_PIN=$(openssl x509 -in "$TLS_DIR/aggregator.crt" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d : | tr A-F a-f)
  fi
}

agg_scheme() { [ "$TLS" = 1 ] && echo https || echo http; }

build_images() {
  docker build -q -t "$AGG_IMAGE" -f aggregator/Dockerfile . >/dev/null
  docker build -q -t "$HOSP_IMAGE" -f hospital/Dockerfile . >/dev/null
  echo "built $AGG_IMAGE and $HOSP_IMAGE"
}

ensure_network() {
  docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
  echo "network $NETWORK ready"
}

container_names() { for s in "${SITES[@]}"; do echo "$NAME_PREFIX$s"; done; }

remove_containers() {
  docker rm -f "$AGG_NAME" $(container_names) >/dev/null 2>&1 || true
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
  TOKEN="$(cat "$TOKEN_FILE")" docker run -d --name "$AGG_NAME" $CPUS --memory "$AGG_MEMORY" \
    --network "$NETWORK" --network-alias aggregator \
    -e TOKEN -e EXPECTED_SITES="$(IFS=,; echo "${SITES[*]}")" -e EXPECTED_ROUNDS="$EXPECTED_ROUNDS" -e INIT_BIAS="$INIT_BIAS" \
    -e STATE_DIR=/state -v "$RUN_DIR/state":/state -e SECURE="$SECURE" -e SECURE_SAMPLE="${SECURE_SAMPLE:-0}" "${tls[@]}" \
    "$AGG_IMAGE" >/dev/null
  wait_until 60 "aggregator healthy" 'agg_get /health | grep -q "\"ok\": true"'
}

start_hospital() {  # start_hospital SITE
  local s=$1 data_mount lib_mount=() data_path
  if [ "$MODE" = real ]; then
    [ -f "${REAL_FILE[$s]}" ] || { echo "MODE=real but ${REAL_FILE[$s]} is missing"; exit 1; }
    [ -d "$PWD/lib/fedsepsis" ] || { echo "MODE=real but ./lib/fedsepsis is missing (run ./setup_real.sh)"; exit 1; }
    [ -f "$BORROWED_MODEL" ] && [ -f "$DEMO_PATIENT" ] || { echo "MODE=real but $BORROWED_MODEL or $DEMO_PATIENT is missing"; exit 1; }
    data_mount="${REAL_FILE[$s]}:/data/$(basename "${REAL_FILE[$s]}"):ro"
    data_path="/data/$(basename "${REAL_FILE[$s]}")"
    lib_mount=(-v "$PWD/lib":/app/lib:ro -v "$BORROWED_MODEL":/models/model_big_asis.json:ro
               -v "$DEMO_PATIENT":/models/demo_patient.json:ro
               -e BORROWED_MODEL_PATH=/models/model_big_asis.json -e DEMO_PATIENT_PATH=/models/demo_patient.json
               -e WAIT_TIMEOUT_S=3600)
    local f
    for kind in EVAL RETUNE; do
      [ "$kind" = EVAL ] && f=${EVAL_FILE[$s]:-} || f=${RETUNE_FILE[$s]:-}
      [ -z "$f" ] && continue
      [ -f "$f" ] || { echo "MODE=real but $f is missing"; exit 1; }
      lib_mount+=(-v "$f":/data/$(basename "$f"):ro -e ${kind}_DATA_PATH=/data/$(basename "$f"))
    done
    f=${NEWS2_FILE[$s]:-}
    if [ -n "$f" ] && [ -f "$f" ] && [ -f "$REAL_DIR/news2_thresholds.json" ]; then
      lib_mount+=(-v "$f":/data/news2_scores.npz:ro -e NEWS2_PATH=/data/news2_scores.npz
                  -v "$REAL_DIR/news2_thresholds.json":/models/news2_thresholds.json:ro
                  -e NEWS2_THRESHOLDS_PATH=/models/news2_thresholds.json)
    fi
  else
    data_mount="$RUN_DIR/data/$s:/data:ro"
    data_path=/data/site.json
  fi
  local tls=()
  [ "$TLS" = 1 ] && tls=(-e TLS_CA=/tls/aggregator.crt -e TLS_PIN_SHA256="${PIN_OVERRIDE:-$TLS_PIN}"
                         -v "$TLS_DIR/aggregator.crt":/tls/aggregator.crt:ro)
  local sec=()
  if [ "$SECURE" = 1 ]; then
    sec=(-e SECURE=1 -e ALL_SITES="$(IFS=,; echo "${SITES[*]}")" -e PAIR_SEED_DIR=/pairs)
    for f in "$PAIR_DIR"/*.seed; do                 # mount ONLY the seeds this site is part of
      case "$(basename "$f" .seed)" in "$s"__*|*__"$s") sec+=(-v "$f":/pairs/$(basename "$f"):ro) ;; esac
    done
  fi
  TOKEN="$(cat "$TOKEN_FILE")" docker run -d --name "$NAME_PREFIX$s" $CPUS --memory "${HOSP_MEMORY[$s]}" --network "$NETWORK" \
    -e TOKEN -e SITE_ID="$s" -e AGGREGATOR_URL="$(agg_scheme)://aggregator:8000" -e EXPECTED_ROUNDS="$EXPECTED_ROUNDS" \
    -e DATA_PATH="$data_path" -e MODE="$MODE" -e THRESHOLD="$THRESHOLD" -e OUT_DIR=/out "${TRAIN_ENV[@]}" \
    -v "$data_mount" -v "$RUN_DIR/out/$s":/out "${lib_mount[@]}" "${tls[@]}" "${sec[@]}" \
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
w = m["w"]
print("final global model (from %s/out/final_model.json): %d weights" % (sites[0], len(w)))
shown = w if len(w) <= 20 else w[:8]
print("  w = [" + ", ".join(f"{v:.6f}" for v in shown) + (", ...]" if len(w) > 20 else "]"))
print(f"  b = {m['b']:.6f}   |w|_max = {max(abs(v) for v in w):.6f}")
same = all(models[s]["model"]["w"] == w and models[s]["model"]["b"] == m["b"] for s in sites)
print("all sites hold the identical final model:", same)
EOF
  echo "=== aggregator log ==="
  docker logs "$AGG_NAME" 2>&1
}

save_results() {  # save_results LABEL RUNTIME_S
  local d=$PWD/results/$1
  rm -rf "$d"; mkdir -p "$d"
  cp "$RUN_DIR/out/${SITES[0]}/final_model.json" "$d/final_model.json"
  docker logs "$AGG_NAME" > "$d/aggregator_log.txt" 2>&1
  for s in "${SITES[@]}"; do docker logs "$NAME_PREFIX$s" > "$d/${s}_log.txt" 2>&1; done
  python3 - "$d/run_info.json" "$1" "$2" "$MODE" "$EXPECTED_ROUNDS" <<'EOF'
import json, sys
p, label, secs, mode, rounds = sys.argv[1:]
json.dump({"label": label, "runtime_seconds": float(secs), "mode": mode, "rounds": int(rounds)}, open(p, "w"), indent=1)
EOF
  echo "results saved in results/$1/"
}
