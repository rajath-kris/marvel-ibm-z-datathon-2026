#!/usr/bin/env bash
# Step 5 tests a-i. Each test writes evidence/test_<x>_*.txt and prints PASS/FAIL.
# Usage: ./tests/run_tests.sh   (runs a fresh stub demo first; leaves the demo running at the end)
set -uo pipefail
cd "$(dirname "$0")/.."
source ./config.sh
source ./demo_lib.sh
require_token
mkdir -p evidence
EV=$PWD/evidence
RESULTS=()
result() { RESULTS+=("$1  $2"); echo ">>> $1  $2"; }
check() { if eval "$2"; then result PASS "$1"; else result FAIL "$1"; fi; }  # check NAME 'condition'

net_curl() {  # curl from a throwaway container on the demo network (no token unless given)
  docker run --rm --network "$NETWORK" --entrypoint curl "$BASE_IMAGE" -s -w ' [HTTP %{http_code}]' "$@"
}

echo "################ (b)(d)(g-unit) aggregator logic unit tests ################"
docker run --rm $LIMITS -v "$PWD":/w:ro --entrypoint /opt/venv/bin/python "$BASE_IMAGE" \
  -B /w/tests/test_aggregator_logic.py 2>&1 | tee "$EV/test_b_d_unit.txt"
check "b+d unit tests (one-round arithmetic [2.5,3.5], duplicate, 409, restore)" \
  'grep -q "^OK$" "$EV/test_b_d_unit.txt"'

echo "################ (c) stub federated run ################"
./run_demo.sh > "$EV/test_c_stub_run.txt" 2>&1
cat "$EV/test_c_stub_run.txt" | sed -n '1,/=== aggregator log ===/p'
STATUS=$(agg_get /status)
echo "final /status: $STATUS" | tee -a "$EV/test_c_stub_run.txt"
check "c all $EXPECTED_ROUNDS rounds complete, done=true" \
  "echo '$STATUS' | python3 -c 'import json,sys; s=json.load(sys.stdin); sys.exit(0 if s[\"round\"]==$EXPECTED_ROUNDS and s[\"done\"] is True else 1)'"

echo "################ (a) auth ################"
{
  echo "\$ curl http://aggregator:8000/status        # from a container on $NETWORK, no X-Token"
  net_curl http://aggregator:8000/status; echo
  echo "\$ curl -H 'X-Token: not-the-token' http://aggregator:8000/global"
  net_curl -H 'X-Token: not-the-token-at-all' http://aggregator:8000/global; echo
  echo "\$ curl -X POST http://aggregator:8000/update   # no token"
  net_curl -X POST -d '{"site_id":"hospA","round":0,"w":[0],"b":0,"n":1}' http://aggregator:8000/update; echo
  echo "\$ curl http://127.0.0.1:8000/health          # from the VM host: aggregator has no published port"
  curl -s -m 3 -w ' [HTTP %{http_code}]' http://127.0.0.1:8000/health; echo " (curl exit $?)"
  echo "\$ (with token, from inside the aggregator container) GET /health"
  agg_get /health; echo
} 2>&1 | tee "$EV/test_a_auth.txt"
check "a no/wrong token -> 401 (3 requests)" '[ "$(grep -c "HTTP 401" "$EV/test_a_auth.txt")" -eq 3 ]'
check "a aggregator not reachable from host" 'grep -q "HTTP 000" "$EV/test_a_auth.txt"'

echo "################ (d) idempotency and ordering against the live aggregator ################"
# Fresh throwaway aggregator on the same network, so the finished demo state is untouched.
docker rm -f fedsepsis-agg-d >/dev/null 2>&1
TOKEN="$(cat "$TOKEN_FILE")" docker run -d --name fedsepsis-agg-d $LIMITS --network "$NETWORK" \
  -e TOKEN -e EXPECTED_SITES=hospA,hospB -e EXPECTED_ROUNDS=3 -e STATE_DIR=/tmp/state "$AGG_IMAGE" >/dev/null
d_req() { docker exec fedsepsis-agg-d sh -c 'curl -s -w " [HTTP %{http_code}]" -H "X-Token: $TOKEN" -H "Content-Type: application/json" -X "$1" ${2:+-d "$2"} "http://127.0.0.1:8000$3"' _ "$@"; echo; }
wait_until 60 "throwaway aggregator" 'docker exec fedsepsis-agg-d sh -c "curl -sf -H \"X-Token: \$TOKEN\" http://127.0.0.1:8000/health" >/dev/null'
{
  d_req POST '{"site_id":"hospA","n":100,"sum":[100,200],"sumsq":[200,500]}' /stats
  d_req POST '{"site_id":"hospB","n":300,"sum":[900,1200],"sumsq":[3000,5000]}' /stats
  echo "# hospA round 0, then the same update again"
  d_req POST '{"site_id":"hospA","round":0,"w":[1,2],"b":0,"n":100}' /update
  d_req POST '{"site_id":"hospA","round":0,"w":[1,2],"b":0,"n":100}' /update
  d_req GET "" /status
  echo "# hospB posts for round 1 while round 0 is open"
  d_req POST '{"site_id":"hospB","round":1,"w":[3,4],"b":0,"n":300}' /update
  d_req POST '{"site_id":"hospB","round":0,"w":[3,4],"b":0,"n":300}' /update
  echo "# expect round 1, w=[2.5,3.5] (duplicate not counted)"
  d_req GET "" /global
} 2>&1 | tee "$EV/test_d_idempotency_live.txt"
docker rm -f fedsepsis-agg-d >/dev/null
check "d duplicate -> ok+duplicate" 'grep -q "\"duplicate\": true} \[HTTP 200\]" "$EV/test_d_idempotency_live.txt"'
check "d wrong round -> 409" 'grep -q "round 1 != current round 0\"} \[HTTP 409\]" "$EV/test_d_idempotency_live.txt"'
check "d weights [2.5,3.5] after duplicate" 'grep -q "\"round\": 1, \"model\": {\"w\": \[2.5, 3.5\]" "$EV/test_d_idempotency_live.txt"'

echo "################ (e) isolation ################"
{
  echo "\$ docker exec hospA ls -la /data /out"
  docker exec hospA ls -la /data /out
  echo "\$ docker inspect hospA  (mounts only)"
  docker inspect hospA --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} (rw={{.RW}}){{"\n"}}{{end}}'
  echo "\$ docker exec hospA: count patient ids by site prefix in /data/site.json"
  docker exec hospA sh -c 'printf "hospA rows: %s\nhospB rows: %s\n" "$(grep -o "\"hospA-[0-9]*\"" /data/site.json | wc -l)" "$(grep -o "\"hospB-[0-9]*\"" /data/site.json | wc -l)"'
  echo "\$ docker exec hospA: every data-sized file (.json >50k or .npz) anywhere in the container, and which contain hospB ids"
  docker exec hospA sh -c 'F=$(find / \( -path /proc -o -path /sys \) -prune -o \( -name "*.npz" -o \( -name "*.json" -size +50k \) \) -type f -print 2>/dev/null); echo "$F"; M=0; for f in $F; do grep -q "hospB-0000" "$f" && { echo "HOSPB DATA IN $f"; M=$((M+1)); }; done; echo "(matches: $M)"'
  echo "\$ docker exec hospA ls /home/linux1/fedsepsis-deploy/run/data/hospB"
  docker exec hospA ls /home/linux1/fedsepsis-deploy/run/data/hospB 2>&1
  echo "\$ docker exec hospA touch /data/x   (expect read-only)"
  docker exec hospA touch /data/x 2>&1
} 2>&1 | tee "$EV/test_e_isolation.txt"
check "e hospA sees no hospB rows" 'grep -q "^hospB rows: 0$" "$EV/test_e_isolation.txt" && grep -q "(matches: 0)" "$EV/test_e_isolation.txt"'
check "e hospA data mount is read-only" 'grep -q "Read-only file system" "$EV/test_e_isolation.txt"'
check "e hospA mounts only its own dirs" '! grep -q "hospB" <(docker inspect hospA --format "{{range .Mounts}}{{.Source}}{{end}}")'

echo "################ (f) allowed fields ################"
{
  echo "request body keys seen by the aggregator during the run (from its log, values are never logged):"
  docker logs "$AGG_NAME" 2>&1 | grep 'event=request_keys' | sed -E 's/.*path=([^ ]*) keys=(.*)/\1 \2/' | sort | uniq -c
  python3 - "$(docker logs "$AGG_NAME" 2>&1)" "$(agg_get /status)" <<'EOF'
import json, re, sys
allowed = {"site_id", "n", "sum", "sumsq", "round", "w", "b"}
seen = set()
for line in sys.argv[1].splitlines():
    m = re.search(r"event=request_keys .* keys=(\S*)", line)
    if m:
        seen |= set(filter(None, m.group(1).split(",")))
persisted = set(json.loads(sys.argv[2])["body_keys_seen"])
print("union of keys in log     :", sorted(seen))
print("aggregator body_keys_seen:", sorted(persisted))
print("allowed                  :", sorted(allowed))
ok = bool(seen) and seen <= allowed and persisted <= allowed
print("ASSERT subset of allowed :", ok)
sys.exit(0 if ok else 1)
EOF
} 2>&1 | tee "$EV/test_f_allowed_fields.txt"
check "f keys seen across run are a subset of allowed" 'grep -q "ASSERT subset of allowed : True" "$EV/test_f_allowed_fields.txt"'

echo "################ (i) score endpoint ################"
{
  for h in 0 5 10 20 39 99; do
    echo "\$ curl -X POST http://127.0.0.1:8101/score -d '{\"patient_id\":\"hospA-0003\",\"hour\":$h}'"
    curl -s -w ' [HTTP %{http_code}]\n' -X POST -d "{\"patient_id\":\"hospA-0003\",\"hour\":$h}" http://127.0.0.1:8101/score
  done
  echo "\$ curl -X POST http://127.0.0.1:8101/score -d '{\"patient_id\":\"hospB-0001\",\"hour\":10}'   # hospB patient at hospA"
  curl -s -w ' [HTTP %{http_code}]\n' -X POST -d '{"patient_id":"hospB-0001","hour":10}' http://127.0.0.1:8101/score
  echo "\$ curl -X POST http://127.0.0.1:8102/score -d '{\"patient_id\":\"hospB-0001\",\"hour\":10}'"
  curl -s -w ' [HTTP %{http_code}]\n' -X POST -d '{"patient_id":"hospB-0001","hour":10}' http://127.0.0.1:8102/score
  echo "\$ curl -X POST http://127.0.0.1:8101/score -d 'not json'"
  curl -s -w ' [HTTP %{http_code}]\n' -X POST -d 'not json' http://127.0.0.1:8101/score
} 2>&1 | tee "$EV/test_i_score.txt"
check "i hospA scores its own patient" '[ "$(grep -c "\"threshold\": .* \[HTTP 200\]" "$EV/test_i_score.txt")" -ge 6 ]'
check "i hospA cannot score a hospB patient (404)" 'grep -q "no rows for that patient.* \[HTTP 404\]" "$EV/test_i_score.txt"'

echo "################ (g) restart aggregator mid-run + (h) docker stats during the run ################"
{
  remove_containers; ensure_network; fresh_run_dirs
  start_aggregator
  start_hospital hospA
  echo "# hospB not started yet, so the run is held in the stats phase"
  wait_until 60 "aggregator has hospA stats" 'agg_get /status | grep -q "\"sites_stats\": \[\"hospA\"\]"'
  echo "status before restart: $(agg_get /status)"
  echo "\$ docker stats --no-stream   (run in progress: hospA waiting for hospB)"
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}' | tee "$EV/docker_stats_during_run.txt"
  echo "\$ docker stop $AGG_NAME && docker start $AGG_NAME"
  docker stop "$AGG_NAME" >/dev/null && docker start "$AGG_NAME" >/dev/null
  wait_until 60 "aggregator healthy after restart" 'agg_get /health | grep -q "\"ok\": true"'
  echo "status after restart : $(agg_get /status)"
  echo "start lines in aggregator log:"; docker logs "$AGG_NAME" 2>&1 | grep 'event=start'
  start_hospital hospB
  wait_done 300
  echo "final status: $(agg_get /status)"
  echo "hospA log (shows it rode out the restart):"; docker logs hospA 2>&1
} 2>&1 | tee "$EV/test_g_restart.txt"
check "g state restored after restart (restored=True, hospA stats kept)" \
  'grep -q "restored=True" "$EV/test_g_restart.txt" && grep -q "status after restart : .*\"sites_stats\": \[\"hospA\"\]" "$EV/test_g_restart.txt"'
check "g run completes after restart" "grep -q 'final status: {\"round\": $EXPECTED_ROUNDS, \"done\": true' \"\$EV/test_g_restart.txt\""

echo "################ (h) docker stats after the run (containers still serving /score) ################"
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}' | tee "$EV/docker_stats_after_run.txt"
check "h docker stats captured" '[ -s "$EV/docker_stats_during_run.txt" ] && [ -s "$EV/docker_stats_after_run.txt" ]'

echo
echo "================ RESULTS ================" | tee "$EV/test_results_summary.txt"
printf '%s\n' "${RESULTS[@]}" | tee -a "$EV/test_results_summary.txt"
! printf '%s\n' "${RESULTS[@]}" | grep -q '^FAIL'
