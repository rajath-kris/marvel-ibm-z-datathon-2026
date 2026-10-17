#!/usr/bin/env bash
# Stretch: 3-site federated training with pairwise-masked secure aggregation (fedsecure/).
# Sites: hospA1 + hospA2 (hospital A split in two by patient: a SIMULATION) and hospB (the small hospital).
# Runs BESIDE the main demo: own container names (sec-*), network, state dir (run/secure) and ports (8111-8113).
#
#   SECURE=1 RUN_LABEL=secure3-masked ./run_secure.sh    # masked (the aggregator never sees one site's update)
#   SECURE=0 RUN_LABEL=secure3-plain  ./run_secure.sh    # same 3 sites, plain FedAvg (baseline for the comparison)
#   SPLIT_MAP=/path/map.json ./run_secure.sh             # split hospital A by Reju's patient-to-ICU map instead
set -euo pipefail
cd "$(dirname "$0")"
SEC_PREFIX=${SEC_PREFIX:-sec-}            # container prefix; a test stack uses another one (e.g. mtest-sec-)
export FED_SITES="hospA1 hospA2 hospB" NAME_PREFIX=$SEC_PREFIX AGG_NAME=${SEC_PREFIX}aggregator MODE=real
export RUN_DIR=${SEC_RUN_DIR:-$PWD/run/secure}
export SECURE_SAMPLE=${SECURE_SAMPLE:-3}   # keep the first 3 masked values each site sent (demo page)
source ./config.sh
NETWORK=${SEC_NETWORK:-fedsecure-net}
source ./demo_lib.sh
HOST_PORT[hospB]=$(( 8113 + ${PORT_OFFSET:-0} ))
# the main demo and Reju's sweep may run alongside; the memory floor still applies
ALLOW_JOBS="${ALLOW_JOBS:+$ALLOW_JOBS,}fedsepsis-aggregator,hospA,hospB,rev-fedsepsis-aggregator,rev-hospA,rev-hospB,hard-.*,fedcurve,fedseed_[0-9]+"

require_token
load_train_config
build_images
remove_containers
preflight
ensure_network
fresh_run_dirs

# hospital A split into two simulated sites (kept in run/secure/split, never committed)
SPLIT_TAG=${SPLIT_MAP:+map-$(basename "$SPLIT_MAP" .json)}; SPLIT_TAG=${SPLIT_TAG:-random-seed2026}
if [ "$(cat "$RUN_DIR/split/TAG" 2>/dev/null)" != "$SPLIT_TAG" ]; then
  mkdir -p "$RUN_DIR/split"
  docker run --rm --cpus 1 --memory 2g -v "$REAL_DIR/site_big.npz":/in/site_big.npz:ro -v "$RUN_DIR/split":/out \
    -v "$PWD/tools":/tools:ro ${SPLIT_MAP:+-v "$SPLIT_MAP":/map.json:ro} --entrypoint /opt/venv/bin/python "$BASE_IMAGE" \
    -I /tools/split_site.py /in/site_big.npz /out hospA1 hospA2 ${SPLIT_MAP:+--map /map.json --drop-unmapped}
  echo "$SPLIT_TAG" > "$RUN_DIR/split/TAG"
else
  echo "keeping split ($SPLIT_TAG)"
fi

if [ "$SECURE" = 1 ]; then
  # fresh pair seeds AND a fresh session id for every secure run, so masks never repeat across runs
  ./setup_pairs.sh --rotate "${SITES[@]}"
  export SECURE_SESSION=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
  echo "secure session: new random id (not printed); pair seeds rotated"
fi

T0=$(date +%s.%N)
start_aggregator
for s in "${SITES[@]}"; do start_hospital "$s"; done
wait_done 7200
T1=$(date +%s.%N)
RUNTIME=$(python3 -c "print(f'{$T1 - $T0:.1f}')")
print_summary
echo "secure=$SECURE split=$SPLIT_TAG runtime=${RUNTIME}s"
[ -n "$RUN_LABEL" ] && save_results "$RUN_LABEL" "$RUNTIME"
echo "Stop with: docker rm -f $AGG_NAME $(container_names | tr '\n' ' ')&& docker network rm $NETWORK"
