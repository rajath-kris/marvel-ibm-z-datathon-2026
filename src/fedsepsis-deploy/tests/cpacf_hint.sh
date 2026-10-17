#!/usr/bin/env bash
# B3 supplementary, NOT the verdict: can OpenSSL on this machine use CPACF at all?
# Method: the same benchmark with and without CPACF; "without" = every CPACF query mask zeroed through OPENSSL_s390xcap
# (the documented list form; the bare value "off" turned out to have no effect on this OpenSSL, measured and discarded).
# A large drop when the masks are zeroed shows OpenSSL uses CPACF for that algorithm. It does NOT show that a given federated
# round used it; only the cpacfstats counters (tests/test_cpacf.sh) can say that.
set -uo pipefail
cd "$(dirname "$0")/.."
EV=$PWD/evidence-hardening; mkdir -p "$EV"
MASK="stfle:0:0:0:0:kimd:0:0:klmd:0:0:km:0:0:kmc:0:0:kmac:0:0:kmctr:0:0:kmo:0:0:kmf:0:0:prno:0:0:kma:0:0:pcc:0:0:kdsa:0:0"
{
  echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)  host openssl: $(openssl version | cut -d' ' -f1-2)  (2 s per run, 16 KB blocks, bytes/s x1000)"
  for alg in aes-256-gcm sha256; do
    for mode in default masked; do
      if [ $mode = masked ]; then export OPENSSL_s390xcap="$MASK"; else unset OPENSSL_s390xcap; fi
      printf '%-12s %-8s ' $alg $mode
      openssl speed -seconds 2 -bytes 16384 -evp $alg 2>/dev/null | awk '/^(AES|sha|SHA|aes)/ && NF>=2 {print $NF}' | tail -1
    done
  done
  unset OPENSSL_s390xcap
  echo "# inside the hardened hospital image (Python 3.12, OpenSSL 3.5.5, the library the federated round uses): hashlib SHA-256"
  for mode in default masked; do
    envs=(); [ $mode = masked ] && envs=(-e "OPENSSL_s390xcap=$MASK")
    printf '%-8s ' $mode
    docker run --rm --name "hard-bench-$mode" --user 1000:1000 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m -e HOME=/tmp "${envs[@]}" \
      --cap-drop ALL --security-opt no-new-privileges:true --memory 512m --cpus 1 --pids-limit 64 --entrypoint /opt/venv/bin/python fedsepsis-hospital:hard \
      -I -c "import hashlib,time,ssl; b=bytes(32*1024*1024); t=time.perf_counter(); [hashlib.sha256(b).digest() for _ in range(8)]; print(round(256/(time.perf_counter()-t)), 'MB/s sha256,', ssl.OPENSSL_VERSION)"
  done
} 2>&1 | tee "$EV/cpacf_indirect_hint.txt"
