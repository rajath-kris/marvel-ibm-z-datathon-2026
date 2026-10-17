#!/usr/bin/env bash
# Verify the real inputs in $REAL_DIR against its SHA256SUMS.txt, then copy the fedsepsis package
# ($LIB_SRC, which must be byte-identical to the checksummed $REAL_DIR/fedsepsis) into ./lib.
# Data and models are NOT copied: they are mounted read-only from $REAL_DIR.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh
[ -s "$REAL_DIR/SHA256SUMS.txt" ] || { echo "STOP: $REAL_DIR/SHA256SUMS.txt not found"; exit 1; }
echo "verifying $REAL_DIR/SHA256SUMS.txt ($(wc -l < "$REAL_DIR/SHA256SUMS.txt") files)"
(cd "$REAL_DIR" && sha256sum -c --strict --quiet SHA256SUMS.txt) || { echo "STOP: checksum mismatch"; exit 1; }
echo "all checksums OK"
for f in __init__.py VERSION test_fedsepsis.py; do
  cmp "$LIB_SRC/$f" "$REAL_DIR/fedsepsis/$f" || { echo "STOP: $LIB_SRC/$f differs from the checksummed copy"; exit 1; }
done
rm -rf lib/fedsepsis && mkdir -p lib/fedsepsis
cp "$LIB_SRC/__init__.py" "$LIB_SRC/VERSION" "$LIB_SRC/test_fedsepsis.py" lib/fedsepsis/
echo "lib/fedsepsis copied from $LIB_SRC (VERSION $(cat lib/fedsepsis/VERSION))"
