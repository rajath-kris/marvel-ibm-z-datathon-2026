#!/usr/bin/env bash
# Throwaway CA + per-container certificates for mutual TLS, generated at setup and stored OUTSIDE the project:
#   $MTLS_DIR/ca.key               CA private key (600). Never mounted into any container.
#   $MTLS_DIR/ca.crt               CA certificate (public): trust anchor on both sides
#   $MTLS_DIR/aggregator.{crt,key} server certificate (CN/SAN aggregator, EKU serverAuth), mounted only into the aggregator
#   $MTLS_DIR/clients/<site>.{crt,key}  one client certificate per hospital (CN = site id, EKU clientAuth), mounted only into that hospital
#   $MTLS_DIR/clients/monitor.{crt,key} used by the test harness health check only
#   $MTLS_DIR/rogue/{ca.crt,client.crt,client.key}  a certificate from an UNTRUSTED CA, for the refusal test
# Keys are mode 600, directories 700, validity 7 days. Nothing here is committed (see .gitignore); nothing is printed except
# subjects and fingerprints.   Usage: ./setup_mtls.sh [--rotate]
set -euo pipefail
cd "$(dirname "$0")"
export SECRET_DIR=${SECRET_DIR:-$HOME/.fedsepsis-hardening}
MTLS_DIR=${MTLS_DIR:-$SECRET_DIR/mtls}
SITES=${MTLS_SITES:-"hospA hospB hospA1 hospA2"}   # both directions use hospA/hospB; the secure demo adds hospA1/hospA2
DAYS=${MTLS_DAYS:-7}
umask 077
if [ -s "$MTLS_DIR/ca.crt" ] && [ "${1:-}" != "--rotate" ]; then
  echo "keeping existing certificates in $MTLS_DIR (use --rotate to replace them)"
else
  rm -rf "$MTLS_DIR"; mkdir -p "$MTLS_DIR/clients" "$MTLS_DIR/rogue"
  new_ca() {  # new_ca DIR CN
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days "$DAYS" -subj "/CN=$2" \
      -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -keyout "$1/ca.key" -out "$1/ca.crt" 2>/dev/null
  }
  issue() {  # issue CA_DIR OUT_PREFIX CN SAN EKU
    local ca=$1 out=$2 cn=$3 san=$4 eku=$5 csr
    csr=$(mktemp)
    openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$cn" -keyout "$out.key" -out "$csr" 2>/dev/null
    openssl x509 -req -in "$csr" -CA "$ca/ca.crt" -CAkey "$ca/ca.key" -CAcreateserial -CAserial "$ca/ca.srl" -days "$DAYS" -out "$out.crt" \
      -extfile <(printf 'subjectAltName=DNS:%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=%s\n' "$san" "$eku") 2>/dev/null
    rm -f "$csr"
  }
  new_ca "$MTLS_DIR" "fedsepsis-hardening-throwaway-ca"
  issue "$MTLS_DIR" "$MTLS_DIR/aggregator" aggregator aggregator serverAuth
  for s in $SITES monitor; do issue "$MTLS_DIR" "$MTLS_DIR/clients/$s" "$s" "$s" clientAuth; done
  new_ca "$MTLS_DIR/rogue" "rogue-ca"
  issue "$MTLS_DIR/rogue" "$MTLS_DIR/rogue/client" hospA hospA clientAuth
  chmod 700 "$MTLS_DIR" "$MTLS_DIR/clients" "$MTLS_DIR/rogue"
  chmod 600 "$MTLS_DIR"/*.key "$MTLS_DIR"/clients/*.key "$MTLS_DIR"/rogue/*.key
  chmod 644 "$MTLS_DIR"/*.crt "$MTLS_DIR"/clients/*.crt "$MTLS_DIR"/rogue/*.crt
  echo "created CA + certificates (valid $DAYS days) in $MTLS_DIR"
fi
for c in "$MTLS_DIR/aggregator.crt" "$MTLS_DIR"/clients/*.crt "$MTLS_DIR/rogue/client.crt"; do
  printf '%-52s ' "${c#$MTLS_DIR/}"
  openssl x509 -in "$c" -noout -subject -issuer -enddate | tr '\n' ' '; echo
done
openssl verify -CAfile "$MTLS_DIR/ca.crt" "$MTLS_DIR/aggregator.crt" "$MTLS_DIR"/clients/*.crt | sed "s#$MTLS_DIR/##"
{ openssl verify -CAfile "$MTLS_DIR/ca.crt" "$MTLS_DIR/rogue/client.crt" 2>&1 || true; } | sed "s#$MTLS_DIR/##" | tail -1 | sed 's/^/(expected to fail) /'
ls -l "$MTLS_DIR" "$MTLS_DIR/clients" | awk 'NF > 8 {print $1, $9}'
