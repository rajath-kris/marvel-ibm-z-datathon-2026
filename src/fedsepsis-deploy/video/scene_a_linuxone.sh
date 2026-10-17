#!/usr/bin/env bash
# Scene a: this really runs on IBM LinuxONE.
source "$(dirname "$0")/common.sh"
title "Dr Sepsis on IBM LinuxONE"
step "Processor architecture"
say "$(uname -m)"
pause
step "Machine type"
say "$(lscpu | awk -F: '/^Machine type/ {gsub(/^ +/, "", $2); print "Machine type: " $2}')"
pause
step "AI accelerator flag (from /proc/cpuinfo)"
if grep -m1 '^features' /proc/cpuinfo | grep -qw nnpa; then say "nnpa: present"; else say "nnpa: not present"; fi
pause
step "Running containers"
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E '^(rev-)?(fedsepsis-aggregator|hospA|hospB)' | sort \
  | awk -F'\t' '{printf "  %-26s %s\n", $1, $2}'
echo
