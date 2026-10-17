#!/usr/bin/env bash
# CPACF evidence on OUR OWN mutual-TLS settings. Runs tests/tls_bulk_bench.py in the hardened hospital image twice:
# default, and with every CPACF query mask zeroed (OPENSSL_s390xcap, the same list form as tests/cpacf_hint.sh).
# Throwaway certificates (EC P-256, like setup_mtls.sh) go to run/cpacf_bench/; the live stack's keys are never used.
# NOT the CPACF verdict: only cpacfstats counters can say a federated round used it (tests/test_cpacf.sh).
set -uo pipefail
cd "$(dirname "$0")/.."
EV=$PWD/evidence-hardening; mkdir -p "$EV"
IMAGE=${BENCH_IMAGE:-fedsepsis-hospital:hard}
CERTS=$PWD/run/cpacf_bench; mkdir -p "$CERTS"
MASK="stfle:0:0:0:0:kimd:0:0:klmd:0:0:km:0:0:kmc:0:0:kmac:0:0:kmctr:0:0:kmo:0:0:kmf:0:0:prno:0:0:kma:0:0:pcc:0:0:kdsa:0:0"
avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
[ "$avail" -ge 2000 ] || { echo "only ${avail} MB available; refusing (shared VM)"; exit 1; }
if [ ! -f "$CERTS/client.crt" ]; then
  ( cd "$CERTS"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 -subj "/CN=bench-ca" -keyout ca.key -out ca.crt 2>/dev/null
    for n in aggregator client; do
      openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$n" -keyout $n.key -out $n.csr 2>/dev/null
      printf 'subjectAltName=DNS:%s\n' "$n" > $n.ext
      openssl x509 -req -in $n.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 2 -extfile $n.ext -out $n.crt 2>/dev/null
    done
    chmod 644 *.crt; chmod 644 *.key )     # container user 1000 = linux1; throwaway 2-day keys, nothing else uses them
fi
{
  echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)  image $IMAGE, hardened flags (non-root, read-only root, caps dropped), 2 cpus"
  for mode in default masked; do
    envs=(); [ $mode = masked ] && envs=(-e "OPENSSL_s390xcap=$MASK")
    echo "## $mode"
    docker run --rm --name "cpacf-bench-$mode" --user 1000:1000 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m -e HOME=/tmp \
      "${envs[@]}" --cap-drop ALL --security-opt no-new-privileges:true --memory 1g --memory-swap 1g --cpus 2 --pids-limit 64 \
      -v "$PWD/tests":/t:ro -v "$CERTS":/certs:ro --entrypoint /opt/venv/bin/python "$IMAGE" -I /t/tls_bulk_bench.py
  done
} 2>&1 | tee "$EV/cpacf_tls_bench.txt"
