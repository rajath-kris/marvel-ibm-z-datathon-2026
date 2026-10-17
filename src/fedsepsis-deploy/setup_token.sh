#!/usr/bin/env bash
# Create the shared token outside the project (once). Never prints it.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh
umask 077
mkdir -p "$SECRET_DIR"
if [ ! -s "$TOKEN_FILE" ]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$TOKEN_FILE"
  echo "created $TOKEN_FILE"
else
  echo "keeping existing $TOKEN_FILE"
fi
printf 'X-Token: %s\n' "$(cat "$TOKEN_FILE")" > "$HDR_FILE"
chmod 600 "$TOKEN_FILE" "$HDR_FILE"
ls -l "$TOKEN_FILE" "$HDR_FILE" | awk '{print $1, $3, $9}'
