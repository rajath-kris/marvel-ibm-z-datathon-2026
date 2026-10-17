#!/usr/bin/env bash
# Step 8 (optional): TLS + certificate pinning for aggregator traffic.
# Runs a full TLS demo, then probes it. Leaves the TLS demo running.
set -uo pipefail
cd "$(dirname "$0")/.."
export TLS=1
source ./config.sh
source ./demo_lib.sh
load_train_config
./setup_tls.sh >/dev/null
require_token
EV=$PWD/evidence; mkdir -p "$EV"
RESULTS=()
check() { if eval "$2"; then RESULTS+=("PASS  $1"); else RESULTS+=("FAIL  $1"); fi; echo ">>> ${RESULTS[-1]}"; }
net() { docker run --rm --network "$NETWORK" -v "$TLS_DIR/aggregator.crt":/pin.crt:ro --entrypoint "$1" "$BASE_IMAGE" "${@:2}"; }

{
  echo "### certificate (public part only)"
  openssl x509 -in "$TLS_DIR/aggregator.crt" -noout -subject -ext subjectAltName -enddate -fingerprint -sha256
  echo
  echo "### TLS=1 ./run_demo.sh"
  ./run_demo.sh 2>&1 | sed -n '1,/=== aggregator log ===/p' | grep -v '=== aggregator log ==='
  echo "aggregator log, first lines:"; docker logs "$AGG_NAME" 2>&1 | head -3
  echo "hospA log:"; docker logs hospA 2>&1 | head -4
  echo
  echo "### 1. plain HTTP to the TLS port (expect failure, no data)"
  echo "\$ curl http://aggregator:8000/health"
  net curl -s -m 5 -w '[HTTP %{http_code}]' http://aggregator:8000/health; echo " (curl exit $?)"
  echo
  echo "### 2. HTTPS without trusting the pinned cert (expect verify failure)"
  echo "\$ curl https://aggregator:8000/health"
  net curl -sS -m 5 https://aggregator:8000/health 2>&1; echo " (curl exit $?)"
  echo
  echo "### 3. HTTPS trusting the pinned cert, no token (expect 401 over TLS 1.3)"
  echo "\$ curl -v --cacert pin.crt https://aggregator:8000/health"
  net curl -s -v -m 5 --cacert /pin.crt -w '[HTTP %{http_code}]\n' https://aggregator:8000/health 2>&1 | grep -E 'SSL connection using|subject:|issuer:|HTTP [0-9]{3}\]|unauthorised'
  echo
  echo "### 4. TLS 1.2 client (expect refused: server is TLS 1.3 only)"
  net curl -sS -m 5 --cacert /pin.crt --tls-max 1.2 https://aggregator:8000/health 2>&1; echo " (curl exit $?)"
  echo
  echo "### 5. hospital with a WRONG pin (expect it refuses to talk to the aggregator)"
  docker rm -f hospA-badpin >/dev/null 2>&1
  TOKEN="$(cat "$TOKEN_FILE")" docker run -d --name hospA-badpin $LIMITS --network "$NETWORK" \
    -e TOKEN -e SITE_ID=hospA -e AGGREGATOR_URL=https://aggregator:8000 -e EXPECTED_ROUNDS=1 \
    -e DATA_PATH=/data/site.json -e TLS_CA=/tls/aggregator.crt \
    -e TLS_PIN_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
    -v "$RUN_DIR/data/hospA":/data:ro -v "$TLS_DIR/aggregator.crt":/tls/aggregator.crt:ro "$HOSP_IMAGE" >/dev/null
  timeout 60 bash -c 'until docker logs hospA-badpin 2>&1 | grep -q training_failed; do sleep 0.5; done'
  docker logs hospA-badpin 2>&1
  docker rm -f hospA-badpin >/dev/null
  echo
  echo "### 6. hospital trusting a DIFFERENT self-signed cert for the same name (expect verify failure)"
  ROGUE=$RUN_DIR/rogue-cert; rm -rf "$ROGUE"; mkdir -p "$ROGUE"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 1 -subj /CN=aggregator \
    -addext subjectAltName=DNS:aggregator -keyout "$ROGUE/k" -out "$ROGUE/c" 2>/dev/null; chmod 644 "$ROGUE/c"
  docker rm -f hospA-rogueca >/dev/null 2>&1
  TOKEN="$(cat "$TOKEN_FILE")" docker run -d --name hospA-rogueca $LIMITS --network "$NETWORK" \
    -e TOKEN -e SITE_ID=hospA -e AGGREGATOR_URL=https://aggregator:8000 -e EXPECTED_ROUNDS=1 \
    -e DATA_PATH=/data/site.json -e TLS_CA=/tls/c -e TLS_PIN_SHA256="$TLS_PIN" \
    -v "$RUN_DIR/data/hospA":/data:ro -v "$ROGUE/c":/tls/c:ro "$HOSP_IMAGE" >/dev/null
  timeout 60 bash -c 'until docker logs hospA-rogueca 2>&1 | grep -q training_failed; do sleep 0.5; done'
  docker logs hospA-rogueca 2>&1
  docker rm -f hospA-rogueca >/dev/null; rm -rf "$ROGUE"
} 2>&1 | tee "$EV/test_tls.txt"

check "TLS demo completes all rounds" "grep -q 'rounds completed: $EXPECTED_ROUNDS / $EXPECTED_ROUNDS   done=True' '$EV/test_tls.txt'"
check "aggregator reports tls_enabled" "grep -q 'event=tls_enabled' '$EV/test_tls.txt'"
check "plain HTTP to TLS port gets no HTTP response" "grep -q '^\[HTTP 000\] (curl exit' '$EV/test_tls.txt'"
check "untrusted cert rejected by curl" "grep -q 'self-signed certificate' '$EV/test_tls.txt'"
check "TLSv1.3 negotiated with pinned cert" "grep -q 'SSL connection using TLSv1.3' '$EV/test_tls.txt'"
check "token still required over TLS (401)" "grep -q '\[HTTP 401\]' '$EV/test_tls.txt'"
check "TLS 1.2 refused" "grep -A1 '### 4' '$EV/test_tls.txt' | grep -qiE 'alert|version|error'"
check "wrong pin -> hospital refuses (PinMismatch)" "grep -q 'training_failed.*PinMismatch' '$EV/test_tls.txt'"
check "rogue cert -> hospital refuses (cert verify)" "grep -q 'training_failed.*CERTIFICATE_VERIFY_FAILED' '$EV/test_tls.txt'"
echo "================ TLS RESULTS ================" | tee "$EV/test_tls_summary.txt"
printf '%s\n' "${RESULTS[@]}" | tee -a "$EV/test_tls_summary.txt"
! printf '%s\n' "${RESULTS[@]}" | grep -q '^FAIL'
