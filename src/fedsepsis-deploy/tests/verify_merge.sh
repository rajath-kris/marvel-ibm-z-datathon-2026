#!/usr/bin/env bash
# Verify the merged (hardened + mutual TLS) code on a SEPARATE test stack, one stack at a time, then remove it:
#   forward  mtest-aggregator, mtest-hospA, mtest-hospB            127.0.0.1:9101 / 9102
#   reverse  mtest-rev-aggregator, mtest-rev-hospB, mtest-rev-hospA  127.0.0.1:9201 / 9202
#   secure   mtest-sec-aggregator, mtest-sec-hospA1/A2/B            (ICU-map split, plain vs masked)
# Own token / CA / pair seeds in ~/.fedsepsis-mtest. Never starts, stops or removes any other container.
set -uo pipefail
cd "$(dirname "$0")/.."
export SECRET_DIR=$HOME/.fedsepsis-mtest HARDEN=1 MTLS=1 MODE=real PORT_OFFSET=1000
export AGG_IMAGE=fedsepsis-aggregator:mtest HOSP_IMAGE=fedsepsis-hospital:mtest   # never the live :dev tags
export ALLOW_JOBS="fedsepsis-aggregator,hospA,hospB,rev-fedsepsis-aggregator,rev-hospA,rev-hospB,sec-.*,hard-.*,mtest-.*,fedseed_[0-9]+"
SECTIONS=${1:-"forward reverse secure"}   # e.g. ./tests/verify_merge.sh secure
OUT=evidence/verify_merge${1:+_$1}.txt; : > "$OUT"; fail=0
log() { echo "$*" | tee -a "$OUT"; }
res() { if [ "$1" = 0 ]; then log "PASS  $2"; else log "FAIL  $2"; fail=1; fi; }
rm_stack() { docker rm -f $(docker ps -a --format '{{.Names}}' | grep -E "^$1") >/dev/null 2>&1; docker network rm "$2" >/dev/null 2>&1; true; }

hardening_checks() {  # hardening_checks PREFIX
  python3 - "$1" <<'PY' | tee -a "$OUT"
import json, subprocess, sys
pre = sys.argv[1]
names = [n for n in subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split() if n.startswith(pre)]
bad = 0
for n in sorted(names):
    i = json.loads(subprocess.run(["docker", "inspect", n], capture_output=True, text=True).stdout)[0]
    h, c = i["HostConfig"], i["Config"]
    ok = (c["User"] == "1000:1000" and h["ReadonlyRootfs"] and h["CapDrop"] == ["ALL"] and not h["CapAdd"]
          and "no-new-privileges:true" in (h["SecurityOpt"] or []) and not h["Privileged"] and h["PidsLimit"] == 256
          and all(b["HostIp"] == "127.0.0.1" for p in (h["PortBindings"] or {}).values() for b in p))
    bad += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {n:24s} User={c['User']} ReadonlyRootfs={h['ReadonlyRootfs']} CapDrop={h['CapDrop']} "
          f"SecurityOpt={h['SecurityOpt']} Pids={h['PidsLimit']} Ports={[b['HostIp'] + ':' + b['HostPort'] for p in (h['PortBindings'] or {}).values() for b in p]}")
sys.exit(1 if bad or not names else 0)
PY
}
mtls_checks() {  # mtls_checks AGG_CONTAINER EXPECTED_CNS...
  local agg=$1; shift; local L; L=$(docker logs "$agg" 2>&1)
  echo "$L" | grep -q "client_cert_required=True"; res $? "$agg: TLS 1.3 server requires a client certificate"
  for cn in "$@"; do echo "$L" | grep -q "event=tls_session .*client_cn=$cn version=TLSv1.3"; res $? "$agg: mutual-TLS session with client_cn=$cn (TLSv1.3)"; done
  echo "$L" | grep -E "event=tls_session" | sed 's/^/        /' | tee -a "$OUT" >/dev/null
}
fields_check() {  # fields_check AGG_CONTAINER
  docker logs "$1" 2>&1 | python3 -c '
import re, sys
allowed = {"site_id", "n", "sum", "sumsq", "round", "w", "b"}; seen = set(); n = 0
for line in sys.stdin:
    m = re.search(r"event=request_keys .* keys=(\S*)", line)
    if m: seen |= set(filter(None, m.group(1).split(","))); n += 1
print(f"  {n} requests, keys {sorted(seen)}"); sys.exit(0 if seen and seen <= allowed else 1)' | tee -a "$OUT"
  res ${PIPESTATUS[1]} "$1: allowed fields only (site_id, n, sum, sumsq, round, w, b)"
}

log "# verify merged hardened + mTLS code on test stacks  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
log "# memory before: MemAvailable $(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo) MB"
./setup_token.sh > /dev/null && ./setup_mtls.sh > run/mtest_setup_mtls.log && log "mTLS test CA + certificates in ~/.fedsepsis-mtest/mtls ($(ls $SECRET_DIR/mtls/clients | grep -c crt) client certs)"

for d in forward reverse; do
  case " $SECTIONS " in *" $d "*) ;; *) continue ;; esac
  if [ $d = forward ]; then P=mtest-; NET=mtest-net; RD=run/mtest; PORT=9102; REAL=$HOME/handoff/outputs/demo; CNS="hospA hospB"
  else P=mtest-rev-; NET=mtest-rev-net; RD=run/mtest-rev; PORT=9202; REAL=$HOME/handoff/outputs/demo_reverse; CNS="hospB hospA"; fi
  log ""; log "=== $d direction ($P*, 127.0.0.1:$PORT)"
  DIRECTION=$d ./setup_real.sh > /dev/null || { res 1 "$d: setup_real (checksums)"; continue; }
  DIRECTION=$d NAME_PREFIX=$P AGG_NAME=${P}aggregator NETWORK=$NET RUN_DIR=$PWD/$RD RUN_LABEL=mtest-$d ./run_demo.sh > run/mtest_$d.log 2>&1
  res $? "$d: end-to-end training round on the hardened mTLS stack"
  grep -E "pre-flight: Mem|train config|rounds completed|identical|runtime" run/mtest_$d.log | sed 's/^/  /' | tee -a "$OUT"
  hardening_checks "$P"; res $? "$d: hardening flags on every ${P}* container (docker inspect)"
  mtls_checks "${P}aggregator" $CNS
  python3 tests/check_direction.py http://127.0.0.1:$PORT "$REAL" > run/mtest_check_$d.txt 2>&1; r=$?
  sed 's/^/  /' run/mtest_check_$d.txt | tee -a "$OUT" >/dev/null; grep -E "PASS|FAIL" run/mtest_check_$d.txt | head -12 | sed 's/^/  /'
  res $r "$d: /audit, /retune, /patient vs the experiment (tests/check_direction.py)"
  fields_check "${P}aggregator"
  rm_stack "$P" "$NET"; log "  removed ${P}* and $NET"
done

if [[ " $SECTIONS " == *" secure "* ]]; then
log ""; log "=== secure 3-site run (mtest-sec-*, ICU-map split, plain vs masked, hardened + mTLS)"
SEC_PREFIX=mtest-sec- SEC_RUN_DIR=run/mtest-secure SEC_NETWORK=mtest-sec-net SPLIT_MAP=$HOME/handoff/icu_map.json \
  ./tests/test_secure_e2e.sh > run/mtest_secure.log 2>&1; r=$?
grep -E "^Ran|^OK|FAIL|^split:|rounds completed|secure=|ASSERT|wrote demo|RUN FAILED|Traceback" run/mtest_secure.log | sed 's/^/  /' | tee -a "$OUT"
grep -q "ASSERT max |masked - plain| <= 1e-9 : True" run/mtest_secure.log; res $? "secure: masked = plain within 1e-9"
grep -q "ASSERT subset of allowed : True" run/mtest_secure.log; res $? "secure: allowed fields only"
hardening_checks mtest-sec-; res $? "secure: hardening flags on every mtest-sec-* container"
mtls_checks mtest-sec-aggregator hospA1 hospA2 hospB
rm_stack mtest-sec- mtest-sec-net; log "  removed mtest-sec-* and mtest-sec-net"
fi
log ""; log "# memory after: MemAvailable $(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo) MB"
[ $fail = 0 ] && log "ALL CHECKS PASSED" || log "SOME CHECKS FAILED"
exit $fail
