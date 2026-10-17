#!/usr/bin/env bash
# Scene d: only counts, sums and weights leave a hospital.
source "$(dirname "$0")/common.sh"
AGG=${AGG:-fedsepsis-aggregator}
title "What crosses the network?"
step "Every field the aggregator received"
docker logs "$AGG" 2>&1 | grep 'event=request_keys' | sed -E 's/.*path=([^ ]*) keys=(.*)/\1 \2/' | sort | uniq -c \
  | awk '{printf "  %4d x %-8s %s\n", $1, $2, $3}'
pause
step "Allowed: site_id, n, sum, sumsq, round, w, b"
docker logs "$AGG" 2>&1 | python3 -c '
import re, sys
allowed = {"site_id", "n", "sum", "sumsq", "round", "w", "b"}
seen = set()
for line in sys.stdin:
    m = re.search(r"event=request_keys .* keys=(\S*)", line)
    if m:
        seen |= set(filter(None, m.group(1).split(",")))
print("  PASS: no patient rows crossed the network" if seen and seen <= allowed else f"  FAIL: unexpected {sorted(seen - allowed)}")'
echo
