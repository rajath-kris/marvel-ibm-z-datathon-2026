#!/usr/bin/env bash
# Start the federated demo: aggregator (no host port) + hospA + hospB (/score on 127.0.0.1:8101 / 8102).
# Usage: ./run_demo.sh                 (stub mode, fresh run)
#        MODE=real ./run_demo.sh       (real library in ./lib, data in ./data/site_big.npz + site_small.npz)
#        EXPECTED_ROUNDS=10 ./run_demo.sh
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh
source ./demo_lib.sh

require_token
build_images
remove_containers
ensure_network
fresh_run_dirs
[ "$MODE" = stub ] && prepare_stub_data
start_aggregator
for s in "${SITES[@]}"; do start_hospital "$s"; done
wait_done 600
print_summary
echo
echo "Demo is up. /score: curl -s -X POST -d '{\"patient_id\":\"hospA-0003\",\"hour\":10}' http://127.0.0.1:8101/score"
echo "Stop with ./stop_demo.sh"
