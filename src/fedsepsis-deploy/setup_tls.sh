#!/usr/bin/env bash
# Optional (Step 8): self-signed cert for the aggregator, stored OUTSIDE the project.
# Key: $SECRET_DIR/tls/aggregator.key (600, mounted read-only into the aggregator only).
# Cert: $SECRET_DIR/tls/aggregator.crt (public, mounted read-only into hospitals as the pin).
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh
umask 077
mkdir -p "$TLS_DIR"
if [ -s "$TLS_DIR/aggregator.crt" ] && [ "${1:-}" != "--rotate" ]; then
  echo "keeping existing cert (use --rotate to replace it)"
else
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 30 \
    -subj "/CN=aggregator" -addext "subjectAltName=DNS:aggregator" \
    -addext "basicConstraints=critical,CA:FALSE" -addext "keyUsage=critical,digitalSignature" \
    -addext "extendedKeyUsage=serverAuth" \
    -keyout "$TLS_DIR/aggregator.key" -out "$TLS_DIR/aggregator.crt" 2>/dev/null
  chmod 600 "$TLS_DIR/aggregator.key"; chmod 644 "$TLS_DIR/aggregator.crt"
  echo "created new self-signed cert"
fi
openssl x509 -in "$TLS_DIR/aggregator.crt" -noout -subject -ext subjectAltName -enddate -fingerprint -sha256
