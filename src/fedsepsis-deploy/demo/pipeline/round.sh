#!/usr/bin/env bash
# Step 3 of the pipeline demo: start the big hospital in the DEMO stack (vid-*) so one federated round runs.
# Same settings as demo/demo.sh (reverse direction, hardened, mutual TLS, 1 round, accelerator off). Touches vid-* only.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
ROOT=$PWD
export MODE=real DIRECTION=reverse HARDEN=1 MTLS=1 EXPECTED_ROUNDS=1
export NAME_PREFIX=vid- NETWORK=vid-net PORT_OFFSET=1000 RUN_DIR=$ROOT/run/vid
export SECRET_DIR=${SECRET_DIR:-$HOME/.fedsepsis}
export AGG_IMAGE=${DEMO_AGG_IMAGE:-fedsepsis-aggregator:dev}
export HOSP_IMAGE=${DEMO_HOSP_IMAGE:-fedsepsis-hospital:nnpa}
export ALLOW_JOBS="fedsepsis-aggregator,hospA,hospB,rev-fedsepsis-aggregator,rev-hospA,rev-hospB,sec-.*,mtest-.*,hard-.*,fedseed_[0-9]+,vid-.*"
source ./config.sh
source ./demo_lib.sh
[ "$NAME_PREFIX" = vid- ] && [ "$AGG_NAME" = vid-fedsepsis-aggregator ] && [ -n "$RUN_DIR" ] || { echo "unexpected demo-stack names; refusing to continue"; exit 1; }
unset NNPA_SCORE NNPA_SCOPE
require_token
load_train_config >/dev/null
start_hospital "$BIG_SITE"
wait_done 300
echo "round finished"
