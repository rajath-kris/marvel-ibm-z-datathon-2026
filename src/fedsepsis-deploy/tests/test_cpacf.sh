#!/usr/bin/env bash
# B3: is the CPACF (on-chip crypto assist) used by a federated round over mutual TLS?
# Rule, fixed before running: read cpacfstats before and after one full mTLS round (fresh stack, rounds from repro_config.json).
#   VERDICT: CPACF USED             only if both readings succeed AND at least one counter increased during the round
#   VERDICT: CPACF NOT CONFIRMED    in every other case, with what was seen
# An optional, clearly separate, NON-counter observation (tests/cpacf_hint.sh): throughput with and without CPACF (capability masks
# zeroed). It shows whether OpenSSL can use CPACF here; it does not show that the round did.
set -uo pipefail
cd "$(dirname "$0")/.."
EV=$PWD/evidence-hardening; mkdir -p "$EV"
read_counters() {  # read_counters FILE
  { echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)  cpacfstats -p all"; timeout 20 cpacfstats -p all 2>&1; echo "# exit code $?"; } > "$1"
  { echo "# cpacfstats -n (all PAI counters, non-zero)"; timeout 20 cpacfstats -n 2>&1; echo "# exit code $?"; } >> "$1"
}
echo "### cpacfstatsd daemon / socket / perf (what is available to this user)"
{
  echo "groups: $(id -nG)"
  echo "daemon process: $(ps -eo pid,user,cmd | grep -i '[c]pacfstatsd' | head -2 | tr '\n' ';')  (empty = not running)"
  ls -l /run/cpacfstatsd_socket 2>&1 | head -1
  echo "systemctl is-active cpacfstatsd: $(systemctl is-active cpacfstatsd 2>&1 | head -1)"
  echo "kernel.perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid)   (4 = no unprivileged perf)"
  echo "PAI event sources: $(ls /sys/bus/event_source/devices | grep -i pai | tr '\n' ' ')"
  grep -m1 -o 'features.*' /proc/cpuinfo | tr ' ' '\n' | grep -E '^(msa|vx|vxe)' | tr '\n' ' '; echo
} 2>&1 | tee "$EV/cpacf_environment.txt"

echo "### before"
read_counters "$EV/cpacfstats_before.txt"; cat "$EV/cpacfstats_before.txt"
echo "### one federated round over mutual TLS (fresh stack)"
T0=$(date -u +%H:%M:%S)
MTLS=1 ./run_hardened.sh > "$EV/cpacf_round_run.txt" 2>&1; RC=$?
T1=$(date -u +%H:%M:%S)
echo "run_hardened.sh MTLS=1 exit $RC, $T0 -> $T1 UTC"; grep -E 'rounds completed|runtime|memory gate|PAUSED' "$EV/cpacf_round_run.txt"
echo "### after"
read_counters "$EV/cpacfstats_after.txt"; cat "$EV/cpacfstats_after.txt"

python3 - "$EV" <<'EOF' | tee "$EV/cpacf_verdict.txt"
import re, sys
ev = sys.argv[1]
def parse(p):
    txt = open(p).read()
    ok = "Can't connect" not in txt and "Can't access domain socket" not in txt
    vals = {}
    for line in txt.splitlines():
        m = re.match(r"\s*([A-Za-z0-9_ ./:-]+?)\s*[:=]?\s+(\d+)\s*$", line)
        if m and not line.startswith("#"):
            vals[m.group(1).strip()] = int(m.group(2))
    return ok, vals
b_ok, b = parse(f"{ev}/cpacfstats_before.txt")
a_ok, a = parse(f"{ev}/cpacfstats_after.txt")
up = sorted(k for k in a if a[k] > b.get(k, 0)) if (a_ok and b_ok) else []
print(f"before readable: {b_ok} ({len(b)} counters); after readable: {a_ok} ({len(a)} counters); counters that increased: {up}")
if b_ok and a_ok and up:
    print("VERDICT: CPACF USED")
else:
    why = ("cpacfstats could not read the counters (daemon not running / no socket; see cpacfstats_before.txt)" if not (b_ok and a_ok)
           else "the counters were readable but none increased during the round")
    print("VERDICT: CPACF NOT CONFIRMED  --  " + why)
EOF

echo "### indirect hint (NOT counters): does OpenSSL use CPACF at all on this machine? (tests/cpacf_hint.sh)"
./tests/cpacf_hint.sh
