#!/usr/bin/env bash
# Remove ONLY the hardened test stack: containers named hard-* and the network hard-net. It never touches the live
# deployment (do not use stop_demo.sh for this: it removes the live network). Images :hard are kept (docker rmi to drop them).
set -uo pipefail
ids=$(docker ps -a --filter "name=^hard-" --format '{{.Names}}' | tr '\n' ' ')
[ -n "$ids" ] && docker rm -f $ids >/dev/null 2>&1
docker network rm hard-net >/dev/null 2>&1 || true
echo "removed containers: ${ids:-none}; network hard-net removed if it existed"
echo "left running (not ours): $(docker ps --format '{{.Names}}' | grep -v '^hard-' | tr '\n' ' ')"
