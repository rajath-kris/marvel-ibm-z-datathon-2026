#!/usr/bin/env bash
# Start the federated demo: aggregator (no host port) + hospA + hospB (/score on 127.0.0.1:8101 / 8102).
# Usage: ./run_demo.sh                                   (stub mode, fresh run, 5 rounds)
#        MODE=real RUN_LABEL=tuned ./run_demo.sh         (real library in ./lib, data from $REAL_DIR,
#                                                         settings from repro_config.json: 40 rounds x 3 epochs)
#        MODE=real EXPECTED_ROUNDS=5 RUN_LABEL=short-demo ./run_demo.sh
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh
source ./demo_lib.sh

require_token
load_train_config
build_images
remove_containers
preflight
ensure_network
fresh_run_dirs
[ "$MODE" = stub ] && prepare_stub_data
T0=$(date +%s.%N)
start_aggregator
for s in "${SITES[@]}"; do start_hospital "$s"; done
wait_done "$([ "$MODE" = real ] && echo 7200 || echo 600)"
T1=$(date +%s.%N)
RUNTIME=$(python3 -c "print(f'{$T1 - $T0:.1f}')")
print_summary
echo "runtime (aggregator start -> all final models written): ${RUNTIME}s"
[ -n "$RUN_LABEL" ] && save_results "$RUN_LABEL" "$RUNTIME"
echo
echo "Demo is up. /health: curl -s http://127.0.0.1:8101/health    Stop with ./stop_demo.sh"
