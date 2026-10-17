#!/usr/bin/env bash
# Step 6: write tidy text captures into evidence/. Run while the demo is up (after run_demo.sh or run_tests.sh).
# Captures never include the token: no `docker inspect` of containers (it shows Env), only of the network.
set -uo pipefail
cd "$(dirname "$0")"
source ./config.sh
source ./demo_lib.sh
mkdir -p evidence
EV=evidence

hdr() { printf '# %s\n# captured %s on %s\n\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(hostname)"; }

{ hdr "uname -m"; uname -m; } > $EV/uname_m.txt
{ hdr "CPU features line from /proc/cpuinfo (lscpu flags)"; grep -m1 '^features' /proc/cpuinfo; echo; lscpu | grep -E '^(Architecture|Byte Order|CPU\(s\)|Model name|Machine type|Hypervisor vendor|Virtualization type|Flags)'; } > $EV/cpu_features.txt
{ hdr "docker ps"; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'; } > $EV/docker_ps.txt
{ hdr "docker network inspect $NETWORK"; docker network inspect "$NETWORK"; } > $EV/docker_network_inspect.txt
{ hdr "aggregator log (docker logs $AGG_NAME)"; docker logs "$AGG_NAME" 2>&1; } > $EV/aggregator_log.txt
for s in "${SITES[@]}"; do { hdr "$s log (docker logs $s)"; docker logs "$s" 2>&1; } > "$EV/${s}_log.txt"; done
{ hdr "docker stats --no-stream"; docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}'; } > $EV/docker_stats.txt
{ hdr "base image Python probe"
  echo "\$ docker run --rm --entrypoint sh $BASE_IMAGE -c \"which python3; ls /opt/venv/bin | head; /opt/venv/bin/python -c 'import numpy,sys; print(numpy.__version__, sys.byteorder)'\""
  docker run --rm $LIMITS --entrypoint sh "$BASE_IMAGE" -c "which python3; ls /opt/venv/bin | head; /opt/venv/bin/python -c 'import numpy,sys; print(numpy.__version__, sys.byteorder)'" 2>&1
} > $EV/base_image_probe.txt
{ hdr "torch NNPA availability (reported as is)"
  echo "\$ docker run --rm --entrypoint /opt/venv/bin/python $BASE_IMAGE -c \"import torch; print(torch.nnpa.is_available(), torch.nnpa.is_telum2_available())\""
  docker run --rm $LIMITS --entrypoint /opt/venv/bin/python "$BASE_IMAGE" -c "import torch; print(torch.nnpa.is_available(), torch.nnpa.is_telum2_available())" 2>&1
} > $EV/torch_nnpa.txt
for s in "${SITES[@]}"; do cp "$RUN_DIR/out/$s/final_model.json" "$EV/${s}_final_model.json" 2>/dev/null; done

# Token leak check: list (never print) any capture that contains the token.
LEAKS=$(grep -rlF -f "$TOKEN_FILE" $EV || true)
{ hdr "token leak check over evidence/"; if [ -z "$LEAKS" ]; then echo "no file in evidence/ contains the token"; else echo "TOKEN FOUND IN:"; echo "$LEAKS"; fi; } > $EV/token_leak_check.txt
cat $EV/token_leak_check.txt
ls -l $EV | awk 'NR>1 {printf "%8s  %s\n", $5, $9}'
