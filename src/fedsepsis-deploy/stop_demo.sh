#!/usr/bin/env bash
# Stop and remove the demo containers and network. Keeps ./run (data, state, outputs).
set -uo pipefail
cd "$(dirname "$0")"
source ./config.sh
source ./demo_lib.sh
remove_containers
docker rm -f fedsepsis-agg-step2 >/dev/null 2>&1 || true
docker network rm "$NETWORK" >/dev/null 2>&1 || true
echo "stopped: $AGG_NAME ${SITES[*]}; removed network $NETWORK"
