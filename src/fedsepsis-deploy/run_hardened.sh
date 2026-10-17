#!/usr/bin/env bash
# Hardened TEST stack, run beside the live deployment without touching it: hard-aggregator, hard-hospA, hard-hospB on the
# private network hard-net, images :hard, its own token and certificates in ~/.fedsepsis-hardening (outside the project).
#
#   ./run_hardened.sh              container hardening only (plain HTTP inside hard-net, like the live demo)
#   MTLS=1 ./run_hardened.sh       hardening + mutual TLS hospital <-> aggregator (run ./setup_mtls.sh first)
#
# Hardening on every container: non-root (1000:1000), read-only root filesystem (tmpfs /tmp), --cap-drop ALL,
# no-new-privileges, pids/memory/CPU limits, data volumes mounted :ro, no published ports except hospB's demo port
# on 127.0.0.1:18102.  Names are all hard-*: this script never starts, stops or removes anything else.
# Memory gate (shared VM): MemAvailable < 2500 MB -> refuse to start; < 3000 MB -> hospitals 1g, aggregator 512m, 5 rounds.
set -euo pipefail
cd "$(dirname "$0")"
export FED_SITES="hospA hospB" NAME_PREFIX=hard- AGG_NAME=hard-aggregator RUN_DIR=$PWD/run/hardened MODE=real HARDEN=1
export SECRET_DIR=${SECRET_DIR:-$HOME/.fedsepsis-hardening}          # own token + certificates, never the live ones
export MTLS=${MTLS:-0}
[ "$MTLS" = 1 ] && export TLS=1
source ./config.sh
NETWORK=hard-net; AGG_IMAGE=fedsepsis-aggregator:hard; HOSP_IMAGE=fedsepsis-hospital:hard
MIN_AVAILABLE_MB=2500
export MTLS_DIR=${MTLS_DIR:-$SECRET_DIR/mtls}
[ "$MTLS" = 1 ] && TLS_DIR=$MTLS_DIR                                    # aggregator.crt/key + leaf pin come from the mTLS directory
source ./demo_lib.sh
declare -A HOST_PORT=([hospB]=18102)                                    # hospA is not published at all

avail=$(free -m | awk '/^Mem:/ {print $7}')
echo "memory gate: free -m available = ${avail} MB"
if [ "$avail" -lt 2500 ]; then echo "PAUSED: available memory ${avail} MB is under 2500 MB; the test stack was NOT started"; exit 4; fi
if [ "$avail" -lt 3000 ]; then
  echo "low-memory profile: hospA 1g, hospB 1g, aggregator 512m, 5 rounds"
  HOSP_MEMORY=([hospA]=1g [hospB]=1g); AGG_MEMORY=512m; EXPECTED_ROUNDS=${EXPECTED_ROUNDS:-5}
else
  echo "standard profile: hospA ${HOSP_MEMORY[hospA]}, hospB ${HOSP_MEMORY[hospB]}, aggregator $AGG_MEMORY, rounds from repro_config.json"
fi
# the live deployment's containers are expected neighbours (the memory floor above still applies)
ALLOW_JOBS=$(docker ps --format '{{.Names}}' | grep -v '^hard-' | paste -sd, - || true); export ALLOW_JOBS

echo "mode: HARDEN=1 MTLS=$MTLS TLS=${TLS:-0}"
require_token
load_train_config
build_images
remove_containers
preflight
ensure_network
fresh_run_dirs
T0=$(date +%s.%N)
start_aggregator
for s in "${SITES[@]}"; do start_hospital "$s"; done
wait_done 7200
T1=$(date +%s.%N)
RUNTIME=$(python3 -c "print(f'{$T1 - $T0:.1f}')")
print_summary
echo "runtime (aggregator start -> all final models written): ${RUNTIME}s   rounds=$EXPECTED_ROUNDS"
echo "hospB demo port: http://127.0.0.1:${HOST_PORT[hospB]}/health   stop with ./stop_hardened.sh"
