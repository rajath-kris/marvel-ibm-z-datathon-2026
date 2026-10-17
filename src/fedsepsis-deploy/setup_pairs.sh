#!/usr/bin/env bash
# Pairwise secrets for secure aggregation: one 32-byte seed per pair of sites, in ~/.fedsepsis/pairs/ (mode 600,
# outside the project, never printed). Each hospital container mounts only the seeds it is part of; the
# aggregator gets none. A production system would agree these with Diffie-Hellman instead of pre-shared files.
# Usage: ./setup_pairs.sh hospA1 hospA2 hospB      (--rotate to replace existing seeds)
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh
PAIR_DIR=${PAIR_DIR:-$SECRET_DIR/pairs}
rotate=0; [ "${1:-}" = --rotate ] && { rotate=1; shift; }
sites=("$@")
umask 077
mkdir -p "$PAIR_DIR"
made=0
for ((i = 0; i < ${#sites[@]}; i++)); do
  for ((j = i + 1; j < ${#sites[@]}; j++)); do
    pair=$(printf '%s\n%s\n' "${sites[i]}" "${sites[j]}" | LC_ALL=C sort | paste -sd_ - | sed 's/_/__/')
    f=$PAIR_DIR/$pair.seed
    if [ ! -s "$f" ] || [ $rotate = 1 ]; then
      python3 -c 'import secrets; print(secrets.token_hex(32))' > "$f"; made=$((made + 1))
    fi
  done
done
chmod 600 "$PAIR_DIR"/*.seed
echo "pair seeds in $PAIR_DIR: $(ls "$PAIR_DIR" | grep -c '\.seed$') ($made new)"
