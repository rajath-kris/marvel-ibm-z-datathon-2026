# Shared settings, sourced by run_demo.sh, stop_demo.sh and the tests. No secrets here.
BASE_IMAGE=registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest
AGG_IMAGE=fedsepsis-aggregator:dev
HOSP_IMAGE=fedsepsis-hospital:dev
NETWORK=fedsepsis-net
LIMITS="--memory 1g --cpus 1"          # one-off helper containers
CPUS="--cpus 1"
AGG_MEMORY=512m                        # per-container memory: see HOSP_MEMORY in demo_lib.sh (hospA 2g, hospB 1g)
MIN_AVAILABLE_MB=3072                  # pre-flight: refuse to start below this MemAvailable (VM is shared)

# MODE=real inputs (read-only): Reju's handoff.
REAL_DIR=${REAL_DIR:-$HOME/handoff/outputs/demo}   # verified against its SHA256SUMS.txt by setup_real.sh
LIB_SRC=${LIB_SRC:-$HOME/handoff/fedsepsis}
RUN_LABEL=${RUN_LABEL:-}               # if set, results are kept in results/<label>/

# The shared token lives OUTSIDE the project, mode 600. Created by setup_token.sh.
SECRET_DIR=${SECRET_DIR:-$HOME/.fedsepsis}
TOKEN_FILE=$SECRET_DIR/token
HDR_FILE=$SECRET_DIR/curl-header   # "X-Token: ..." for curl -H @file, so the token never hits a command line

# Optional TLS for aggregator traffic (Step 8): TLS=1 ./run_demo.sh  (run ./setup_tls.sh once first)
TLS=${TLS:-0}
TLS_DIR=$SECRET_DIR/tls

EXPECTED_ROUNDS=${EXPECTED_ROUNDS:-}   # default: 5 in stub mode, repro_config.json setup4.rounds in real mode
MODE=${MODE:-stub}
THRESHOLD=${THRESHOLD:-0.5}
