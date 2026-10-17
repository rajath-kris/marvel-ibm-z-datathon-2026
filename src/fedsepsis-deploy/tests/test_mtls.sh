#!/usr/bin/env bash
# B2: mutual TLS refusal tests against the RUNNING mTLS test stack (start it with ./tests/test_hardening.sh mtls).
# Probes come from throwaway hard-probe-* containers on hard-net (hardened the same way). The token is passed as a header FILE
# (never on a command line); curl is never run with -v here, so no header is printed.
set -uo pipefail
cd "$(dirname "$0")/.."
export SECRET_DIR=${SECRET_DIR:-$HOME/.fedsepsis-hardening}
source ./config.sh
MTLS_DIR=${MTLS_DIR:-$SECRET_DIR/mtls}
EV=$PWD/evidence-hardening; mkdir -p "$EV"
OUT=$EV/test_mtls.txt
RES=()
check() { if eval "$2"; then RES+=("PASS  $1"); else RES+=("FAIL  $1"); fi; echo ">>> ${RES[-1]}"; }
docker inspect hard-aggregator >/dev/null 2>&1 || { echo "STOP: hard-aggregator is not running; run ./tests/test_hardening.sh mtls first"; exit 4; }
docker exec hard-aggregator printenv TLS_CLIENT_CA >/dev/null 2>&1 || { echo "STOP: the running stack is not the mTLS one"; exit 4; }

n=0
probe() {  # probe LABEL curl-args...   -> prints the curl output and exit code
  n=$((n + 1))
  docker run --rm --name "hard-probe-$n" --network hard-net --user 1000:1000 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m -e HOME=/tmp \
    --cap-drop ALL --security-opt no-new-privileges:true --memory 128m --cpus 0.5 --pids-limit 64 \
    -v "$MTLS_DIR/ca.crt":/tls/ca.crt:ro \
    -v "$MTLS_DIR/clients/hospA.crt":/tls/hospA.crt:ro -v "$MTLS_DIR/clients/hospA.key":/tls/hospA.key:ro \
    -v "$MTLS_DIR/rogue/client.crt":/tls/rogue.crt:ro -v "$MTLS_DIR/rogue/client.key":/tls/rogue.key:ro \
    -v "$HDR_FILE":/hdr:ro --entrypoint curl "$BASE_IMAGE" -sS -m 10 -w ' [HTTP %{http_code}]' "${@:2}" 2>&1
  echo " (curl exit ${PIPESTATUS[0]:-$?})"
}
U=https://aggregator:8000

{
  echo "### 1. valid CA but NO client certificate (expect refusal)"
  echo "\$ curl --cacert ca.crt $U/health"
  probe x --cacert /tls/ca.crt $U/health
  echo
  echo "### 2. client certificate from an UNTRUSTED CA (expect refusal)"
  echo "\$ curl --cacert ca.crt --cert rogue.crt --key rogue.key $U/health"
  probe x --cacert /tls/ca.crt --cert /tls/rogue.crt --key /tls/rogue.key $U/health
  echo
  echo "### 3. valid client certificate, NO token (expect TLS ok, then 401)"
  echo "\$ curl --cacert ca.crt --cert hospA.crt --key hospA.key $U/health"
  probe x --cacert /tls/ca.crt --cert /tls/hospA.crt --key /tls/hospA.key $U/health
  echo
  echo "### 4. valid client certificate + token (expect 200)"
  echo "\$ curl --cacert ca.crt --cert hospA.crt --key hospA.key -H @token-header-file $U/health"
  probe x --cacert /tls/ca.crt --cert /tls/hospA.crt --key /tls/hospA.key -H @/hdr $U/health
  echo
  echo "### 5. hospA's certificate posting as site hospB (expect 403: certificate CN must equal site_id)"
  echo "\$ curl -X POST --cert hospA.crt ... -d '{\"site_id\":\"hospB\",...}' $U/stats"
  probe x --cacert /tls/ca.crt --cert /tls/hospA.crt --key /tls/hospA.key -H @/hdr -H 'Content-Type: application/json' \
    -X POST -d '{"site_id":"hospB","n":1,"sum":[0],"sumsq":[0]}' $U/stats
  echo
  echo "### 6. TLS 1.2 client with a valid certificate (expect refusal: TLS 1.3 only)"
  probe x --cacert /tls/ca.crt --cert /tls/hospA.crt --key /tls/hospA.key --tls-max 1.2 $U/health
  echo
  echo "### 7. plain HTTP to the TLS port (expect no HTTP response)"
  probe x http://aggregator:8000/health
  echo
  echo "### aggregator log: negotiated TLS sessions, refused connections, CN mismatch"
  docker logs hard-aggregator 2>&1 | grep -E 'event=(tls_enabled|tls_session|connection_error|cert_site_mismatch)' | sed -E 's/^[0-9TZ:-]+ //' | sort | uniq -c
  echo
  echo "### hospital logs: TLS parameters seen by each client"
  for s in hospA hospB; do echo "hard-$s: $(docker logs hard-$s 2>&1 | grep -m1 'event=tls_connected' | sed -E 's/^[0-9TZ:-]+ //')"; done
} 2>&1 | tee "$OUT"

check "no client certificate is refused" "grep -A2 '### 1\.' '$OUT' | grep -q 'alert.*certificate required\|certificate required\|handshake\|SSL_read\|SSL connect' && ! sed -n '/### 1\./,/### 2\./p' '$OUT' | grep -q 'HTTP 200'"
check "certificate from an untrusted CA is refused" "sed -n '/### 2\./,/### 3\./p' '$OUT' | grep -qi 'unknown ca\|bad certificate\|alert\|handshake\|SSL_read\|SSL connect' && ! sed -n '/### 2\./,/### 3\./p' '$OUT' | grep -q 'HTTP 200'"
check "valid certificate without token -> 401" "sed -n '/### 3\./,/### 4\./p' '$OUT' | grep -q 'HTTP 401'"
check "valid certificate + token -> 200" "sed -n '/### 4\./,/### 5\./p' '$OUT' | grep -q 'HTTP 200'"
check "site_id must match the certificate CN -> 403" "sed -n '/### 5\./,/### 6\./p' '$OUT' | grep -q 'HTTP 403'"
check "TLS 1.2 refused" "sed -n '/### 6\./,/### 7\./p' '$OUT' | grep -qi 'alert\|protocol version\|handshake\|SSL_read\|SSL connect' && ! sed -n '/### 6\./,/### 7\./p' '$OUT' | grep -q 'HTTP 200'"
check "plain HTTP gets no HTTP response" "sed -n '/### 7\./,/### aggregator/p' '$OUT' | grep -q 'HTTP 000'"
check "aggregator logs a TLS 1.3 session with a client CN and a cipher" "grep -q 'event=tls_session.*client_cn=hospA.*version=TLSv1.3.*cipher=TLS_' '$OUT'"
check "aggregator logged refused connections" "grep -q 'connection_error' '$OUT'"
check "hospitals present their certificates (client_cert=True)" "[ \"\$(grep -c 'event=tls_connected.*client_cert=True' '$OUT')\" -eq 2 ]"
echo "================ mTLS RESULTS ================" | tee "$EV/summary_mtls_refusal.txt"
printf '%s\n' "${RES[@]}" | tee -a "$EV/summary_mtls_refusal.txt"
! printf '%s\n' "${RES[@]}" | grep -q '^FAIL'
