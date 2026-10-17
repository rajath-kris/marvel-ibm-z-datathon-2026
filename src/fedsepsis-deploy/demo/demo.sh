#!/usr/bin/env bash
# Terminal demo for the submission video: Dr Sepsis on IBM LinuxONE. Real commands, real services, nothing faked.
#
#   demo/demo.sh          run the whole demo (starts its own demo stack first if needed, removes it at the end)
#   demo/demo.sh prep     only start the demo stack (aggregator + the small hospital), so the recording starts after the wait
#   demo/demo.sh down     remove the demo stack (only containers named vid-*, and the network vid-net)
#
#   DEMO_FAST=1   no sleeps, no clear (dry run)        DEMO_NNPA=1   score on the NNPA accelerator (default: CPU path = exact experiment numbers)
#   DEMO_HOLD=3   seconds to hold each finished step   KEEP_STACK=1  leave the demo stack running afterwards
#   DEMO_COLOR=1  force colours when output is not a terminal    DEMO_TIMING=1  print the timing table at the end
#
# What it runs. The LIVE stacks (hospA, hospB, rev-*, fedsepsis-aggregator, notebook) are only READ here (docker ps, docker inspect,
# docker logs). /retune stores a threshold inside the hospital container and a federated round needs fresh containers, so steps 3-5
# run against a separate demo stack: containers vid-fedsepsis-aggregator, vid-hospA, vid-hospB on the network vid-net, ports
# 127.0.0.1:9201-9202, same images' code, same settings as the live stacks (hardened, mutual TLS), reverse direction (small hospital = A).
# Nothing is restarted, rebuilt or stopped that the demo did not start itself. Tokens and certificates are never printed.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT=$PWD
MODE_ARG=${1:-run}

# ---- look ------------------------------------------------------------------------------------------------------------------
FAST=${DEMO_FAST:-0}
if [ -z "${NO_COLOR:-}" ] && { [ -t 1 ] || [ "${DEMO_COLOR:-0}" = 1 ]; }; then
  RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; DIM=$'\e[2m'; BLD=$'\e[1m'; RST=$'\e[0m'
else
  RED=; GRN=; YEL=; DIM=; BLD=; RST=
fi
SLEPT=0
nap()  { [ "$FAST" = 1 ] && return 0; sleep "$1"; SLEPT=$(python3 -c "print($SLEPT + $1)"); }
now()  { date +%s.%N; }
say()  { printf '  %s\n' "$*"; }
dim()  { printf '  %s%s%s\n' "$DIM" "$*" "$RST"; }
STEP=start; STEP_T0=$(now); STEP_S0=0
TIMES=()
end_step() {
  local t1; t1=$(now)
  TIMES+=("$(python3 -c "print('%-60s %5.1f s' % ('$STEP', $t1 - $STEP_T0 - ($SLEPT - $STEP_S0)))")")
}
banner() {
  [ "$STEP" != start ] && end_step
  STEP=$1; STEP_T0=$(now); STEP_S0=$SLEPT
  if [ "$FAST" != 1 ] && [ -t 1 ]; then clear; fi
  echo; echo "${BLD}=== $1 ===${RST}"; echo
  nap 1.5
}
hold() { nap "${DEMO_HOLD:-3}"; }

# ---- fail loudly -----------------------------------------------------------------------------------------------------------
SHOWN=0; FAILCMD=; FAILLINE=
exec 3>&2          # the real stderr, so a failure inside a redirected block (the demo-stack log) is still shown on screen
trap 'FAILCMD=$BASH_COMMAND; FAILLINE=$LINENO' ERR
trap 'rc=$?; if [ $rc -ne 0 ] && [ $SHOWN = 0 ]; then
        echo >&3; echo "${RED}FAILED in step \"$STEP\" (exit $rc, demo.sh line ${FAILLINE:-?}): ${FAILCMD:-exit}${RST}" >&3
        [ -s "${PREPLOG:-/nonexistent}" ] && { echo "${RED}--- last lines of the demo-stack log ---${RST}" >&3; tail -6 "$PREPLOG" >&3; }
        echo "${RED}Stopped. Nothing was faked. The demo stack (vid-*) is left for diagnosis: demo/demo.sh down removes it.${RST}" >&3
      fi' EXIT
die() { SHOWN=1; echo >&3; echo "${RED}FAILED in step \"$STEP\": $*${RST}" >&3; exit 1; }

# ---- demo stack settings (everything before sourcing is exported so config.sh / demo_lib.sh pick it up) -------------------------
export MODE=real DIRECTION=reverse HARDEN=1 MTLS=1 EXPECTED_ROUNDS=1
export NAME_PREFIX=vid- NETWORK=vid-net PORT_OFFSET=1000 RUN_DIR=$ROOT/run/vid
export SECRET_DIR=${SECRET_DIR:-$HOME/.fedsepsis}
export AGG_IMAGE=${DEMO_AGG_IMAGE:-fedsepsis-aggregator:dev}
export HOSP_IMAGE=${DEMO_HOSP_IMAGE:-fedsepsis-hospital:nnpa}
export ALLOW_JOBS="fedsepsis-aggregator,hospA,hospB,rev-fedsepsis-aggregator,rev-hospA,rev-hospB,sec-.*,mtest-.*,hard-.*,fedseed_[0-9]+,vid-.*"
# shellcheck disable=SC1091
source ./config.sh
# shellcheck disable=SC1091
source ./demo_lib.sh
[ "$NAME_PREFIX" = vid- ] && [ "$AGG_NAME" = vid-fedsepsis-aggregator ] && [ -n "$RUN_DIR" ] || die "unexpected demo-stack names; refusing to continue"
URL=http://127.0.0.1:${HOST_PORT[$SMALL_SITE]}          # the small hospital of the demo stack (hospital A, reverse direction)
PREPLOG=$RUN_DIR/prep.log
mkdir -p "$RUN_DIR"

# NNPA scoring (optional path from NNPA.md): only if the image has it. Injected for the small hospital only; demo_lib.sh is not touched.
NNPA_ENV=()
if [ "${DEMO_NNPA:-0}" = 1 ] && command docker run --rm --entrypoint /bin/ls "$HOSP_IMAGE" /app/nnpa_scoring.py >/dev/null 2>&1; then
  NNPA_ENV=(-e NNPA_SCORE=1 -e NNPA_SCOPE=all)
  HOSP_MEMORY[$SMALL_SITE]=2g
fi
docker() {
  if [ "${1:-}" = run ] && [ "${2:-}" = -d ] && [ "${3:-}" = --name ] && [ "${4:-}" = "$NAME_PREFIX$SMALL_SITE" ] && [ ${#NNPA_ENV[@]} -gt 0 ]; then
    shift 2
    command docker run -d "${NNPA_ENV[@]}" "$@"
  else
    command docker "$@"
  fi
}

stack_down() { remove_containers; command docker network rm "$NETWORK" >/dev/null 2>&1 || true; }
stack_running() { [ "$(command docker inspect -f '{{.State.Running}}' "$NAME_PREFIX$SMALL_SITE" 2>/dev/null)" = true ] \
                  && ! command docker inspect "$NAME_PREFIX$BIG_SITE" >/dev/null 2>&1; }
prep() {
  : > "$PREPLOG"
  stack_down >>"$PREPLOG" 2>&1
  {
    require_token; load_train_config
    ( preflight )
    ensure_network; fresh_run_dirs
    start_aggregator; start_hospital "$SMALL_SITE"
    wait_until 120 "$SMALL_SITE data loaded" "curl -s $URL/health | grep -q small_test_patients.npz"
  } >>"$PREPLOG" 2>&1
}

# ---- helpers ---------------------------------------------------------------------------------------------------------------
post() {   # post PATH JSON -> body on stdout; stops loudly on a curl failure or an {"error": ...} answer
  local out msg
  out=$(curl -s --max-time 90 -X POST -d "$2" "$URL$1") || die "POST $1 did not answer"
  msg=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("error",""))' "$out") || die "POST $1 returned something that is not JSON"
  [ -z "$msg" ] || die "POST $1 returned an error: $msg"
  printf '%s' "$out"
}
jget() { python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(eval(sys.argv[2]))' "$1" "$2"; }
bar() {    # bar LABEL VALUE SCALE COLOUR   (36 characters = SCALE alarms a day)
  python3 -c '
import sys
label, v, scale, col, rst = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]
n = max(1, round(36 * v / scale))
print("  %-8s %s%s%s %5.1f a day" % (label, col, "█" * n, rst, v))' "$1" "$2" "$3" "$4" "$RST"
}
perday() { python3 -c "print($1 / 5.0)"; }    # 20 beds = 20 patient-days a day, so per 100 patient-days / 5
logev() {  # logev CONTAINER 'event|names'  -> short log lines (time + event + fields), width <= 100
  command docker logs "$1" 2>&1 | grep -E "event=($2)( |$)" | sed -E 's/^[0-9-]+T([0-9:]+)Z /\1 /; s/ (site|round)=-//g; s/ (restored|secure|stub|nan_free|mode|seconds|path|duplicate|done)=[^ ]*//g' | cut -c1-100
}

# ---- subcommands -----------------------------------------------------------------------------------------------------------
case "$MODE_ARG" in
  down) stack_down; echo "demo stack removed (vid-* containers and network vid-net only)"; SHOWN=1; exit 0 ;;
  prep) T=$(now); prep; echo "demo stack ready in $(python3 -c "print('%.0f' % ($(now) - $T))") s: vid-fedsepsis-aggregator + vid-$SMALL_SITE waiting for vid-$BIG_SITE"; SHOWN=1; exit 0 ;;
  run) ;;
  *) echo "usage: demo/demo.sh [run|prep|down]"; SHOWN=1; exit 2 ;;
esac

# =============================================================================================================================
require_token; load_train_config >/dev/null
banner "PREPARING THE DEMO STACK"
if stack_running; then
  say "demo stack already prepared"
else
  say "starting vid-fedsepsis-aggregator and vid-$SMALL_SITE (hardened, mutual TLS) ..."
  prep
  say "ready"
fi

banner "WHERE THIS RUNS"
say "host         $(hostname)"
say "arch         $(uname -m)"
say "machine type $(awk '/^Type:/ {print $2; exit}' /proc/sysinfo)   model $(awk '/^Model:/ {print $2, $3; exit}' /proc/sysinfo)   (IBM, from /proc/sysinfo)"
lscpu | awk -F: '/^(Vendor ID|Byte Order|CPU\(s\)|CPU dynamic MHz|Hypervisor):/ {gsub(/^ +/, "", $2); k=tolower($1); sub(/cpu dynamic mhz/, "cpu mhz", k); printf "  %-12s %s\n", k, $2}'
LM=$(lscpu | awk -F: '/^Model name:/ {gsub(/^ +/, "", $2); print $2}')
if [ -z "$LM" ] || [ "$LM" = "-" ]; then say "lscpu model  not reported by this z/VM guest (model 702 ME1 is in /proc/sysinfo above)"; else say "lscpu model  $LM"; fi
if grep -m1 '^features' /proc/cpuinfo | grep -qw nnpa; then say "accelerator  nnpa flag present in /proc/cpuinfo"; fi
echo
command docker ps --format 'table {{.Names}}\t{{.Status}}'
hold

banner "SECURITY"
say "Hospital containers on this machine (docker inspect, selected fields only):"
for c in hospA hospB rev-hospA rev-hospB; do
  command docker inspect "$c" --format '{{.Name}}|{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{.HostConfig.CapDrop}}|{{.HostConfig.SecurityOpt}}' \
    | sed 's#^/##' | awk -F'|' '{printf "  %-10s user %-10s read-only root fs: %-5s caps dropped: %-6s %s\n", $1, $2, $3, $4, $5}'
done
echo
say "Mutual TLS, aggregator log:"
command docker logs rev-fedsepsis-aggregator 2>&1 | grep 'event=tls_enabled' | sed -n '1p' | sed -E 's/^[0-9-]+T([0-9:]+)Z /\1 /' | cut -c1-100 | sed 's/^/  /'
echo
say "On-chip crypto (CPACF), TLS 1.3 benchmark on our own mutual-TLS settings:"
python3 - "$ROOT/evidence-hardening/cpacf_tls_bench.txt" <<'PY' || die "CPACF benchmark result file missing or unreadable (evidence-hardening/cpacf_tls_bench.txt)"
import re, sys
sec, cur = {}, None
for l in open(sys.argv[1]):
    if l.startswith("## "):
        cur = l[3:].strip(); sec[cur] = {}
    elif cur:
        for key, pat in (("bulk", r"bulk.*median (\d+) MB/s"), ("hs", r"handshakes: (\d+)/s")):
            m = re.search(pat, l)
            if m: sec[cur][key] = int(m.group(1))
d, m = sec["default"], sec["masked"]
print("  bulk transfer     %5d MB/s with CPACF   %4d MB/s masked   = %.1fx faster" % (d["bulk"], m["bulk"], d["bulk"] / m["bulk"]))
print("  new connections   %5d /s   with CPACF   %4d /s    masked   = %.1fx faster" % (d["hs"], m["hs"], d["hs"] / m["hs"]))
print("  \033[2mbenchmark only: the hardware counters could not be read, so we do not claim our own run used CPACF\033[0m")
PY
hold

banner "STEP 1: SHADOW MODE (borrowed model, no labels)"
say "A small hospital runs a big hospital's model as it is, silently, on its own patients."
say "POST $URL/audit  {\"model\":\"borrowed\"}"
echo
R1=$(post /audit '{"model":"borrowed"}')
RATE1=$(jget "$R1" 'd["alerts_per_100_patient_days"]')
THR1=$(jget "$R1" 'round(d["threshold"], 4)')
DAY1=$(perday "$RATE1"); TARGET=$(perday 25)
SCALE=$DAY1
say "Alarms per day on a 20-bed ward:"
bar before "$DAY1" "$SCALE" "$RED"
bar target "$TARGET" "$SCALE" "$YEL"
echo
dim "measured: $(printf '%.1f' "$RATE1") alerts per 100 patient-days on $(jget "$R1" 'format(d["patients"], ",")') patients, threshold $THR1"
if command docker logs "$NAME_PREFIX$SMALL_SITE" 2>&1 | grep -q 'event=nnpa_scored'; then
  dim "$(command docker logs "$NAME_PREFIX$SMALL_SITE" 2>&1 | grep 'event=nnpa_scored' | tail -1 | sed -E 's/^[0-9-]+T([0-9:]+)Z /\1 /' | cut -c1-96)"
fi
hold

banner "STEP 2: RE-TUNE LOCALLY (budget 25 per 100 patient-days)"
say "POST $URL/retune  {\"model\":\"borrowed\",\"budget\":25}"
echo
R2=$(post /retune '{"model":"borrowed","budget":25}')
THR2=$(jget "$R2" 'round(d["threshold"], 4)')
R3=$(post /audit '{"model":"retuned"}')
printf '%s\n%s\n%s\n' "$R1" "$R2" "$R3" > "$RUN_DIR/responses.json"     # raw service answers, kept for the record
RATE3=$(jget "$R3" 'd["alerts_per_100_patient_days"]')
DAY3=$(perday "$RATE3")
say "Alarms per day on a 20-bed ward:"
bar before "$DAY1" "$SCALE" "$RED"
bar after  "$DAY3" "$SCALE" "$GRN"
bar target "$TARGET" "$SCALE" "$YEL"
echo
dim "measured: $(printf '%.1f' "$RATE1") -> $(printf '%.1f' "$RATE3") alerts per 100 patient-days, threshold $THR1 -> $THR2, no labels used"
hold

banner "STEP 3: ONE FEDERATED ROUND"
say "Hospital A is already up and waiting. Starting hospital B now; they exchange only aggregates."
echo
T=$(now)
{ start_hospital "$BIG_SITE"; wait_done 300; } >>"$PREPLOG" 2>&1
say "round finished in $(python3 -c "print('%.1f' % ($(now) - $T))") s (data load included)"
echo
say "aggregator"
logev "$AGG_NAME" 'start|tls_enabled|stats|update|round_complete' | sed 's/^/  /'
for s in "$SMALL_SITE" "$BIG_SITE"; do
  echo; say "hospital ${s#hosp}"
  logev "$NAME_PREFIX$s" 'data_loaded|stats_posted|update_posted|final_model_written' | sed 's/^/  /'
done
hold

banner "NETWORK CHECK: WHAT CROSSED THE NETWORK"
say "Every request body the aggregator received, by path and field names:"
command docker logs "$AGG_NAME" 2>&1 | grep 'event=request_keys' | sed -E 's/.*path=([^ ]*) keys=(.*)/\1 \2/' | sort | uniq -c \
  | awk '{printf "  %4d x %-8s %s\n", $1, $2, $3}'
echo
CHECK=$(command docker logs "$AGG_NAME" 2>&1 | python3 -c '
import re, sys
meaning = {"site_id": "which hospital", "n": "number of rows (a count)", "sum": "per-feature sums", "sumsq": "per-feature sums of squares",
           "round": "round number", "w": "model weights", "b": "model bias"}
seen = set()
for line in sys.stdin:
    m = re.search(r"event=request_keys .* keys=(\S*)", line)
    if m: seen |= set(filter(None, m.group(1).split(",")))
if not seen or not seen <= set(meaning):
    print("FAIL unexpected fields: %s" % sorted(seen - set(meaning))); sys.exit(1)
for k in ("site_id", "n", "sum", "sumsq", "round", "w", "b"):
    if k in seen: print("  field  %-8s %s" % (k, meaning[k]))
print("PASS")') || { echo "$CHECK"; die "network check failed: a field outside the allowed list reached the aggregator"; }
echo "${CHECK%PASS}"
echo "  ${GRN}${BLD}PASS${RST}  ${GRN}only counts, sums and model weights crossed the network: 0 patient rows${RST}"
hold
end_step

# ---- wrap up ---------------------------------------------------------------------------------------------------------------
for c in "$AGG_NAME" "$NAME_PREFIX$SMALL_SITE" "$NAME_PREFIX$BIG_SITE"; do command docker logs "$c" > "$RUN_DIR/$c.log" 2>&1 || true; done
if [ "${KEEP_STACK:-0}" != 1 ]; then stack_down; fi
if [ "$FAST" = 1 ] || [ "${DEMO_TIMING:-0}" = 1 ]; then
  echo; echo "TIMING (real work per step, sleeps excluded):"; printf '  %s\n' "${TIMES[@]}"
fi
SHOWN=1
