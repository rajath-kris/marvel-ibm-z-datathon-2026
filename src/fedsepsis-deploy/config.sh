# Shared settings, sourced by run_demo.sh, stop_demo.sh and the tests. No secrets here.
BASE_IMAGE=registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest
AGG_IMAGE=fedsepsis-aggregator:dev
HOSP_IMAGE=fedsepsis-hospital:dev
NETWORK=fedsepsis-net
LIMITS="--memory 1g --cpus 1"

# The shared token lives OUTSIDE the project, mode 600. Created by setup_token.sh.
SECRET_DIR=${SECRET_DIR:-$HOME/.fedsepsis}
TOKEN_FILE=$SECRET_DIR/token
HDR_FILE=$SECRET_DIR/curl-header   # "X-Token: ..." for curl -H @file, so the token never hits a command line

# Optional TLS for aggregator traffic (Step 8): TLS=1 ./run_demo.sh  (run ./setup_tls.sh once first)
TLS=${TLS:-0}
TLS_DIR=$SECRET_DIR/tls

EXPECTED_ROUNDS=${EXPECTED_ROUNDS:-5}
MODE=${MODE:-stub}
THRESHOLD=${THRESHOLD:-0.5}
