# Shared helpers for the video scenes. Short lines, no noise, a pause between steps.
# NO_PAUSE=1 runs straight through (for testing). Run from anywhere: scripts cd to the project.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
B=$( [ -t 1 ] && tput bold || true ); N=$( [ -t 1 ] && tput sgr0 || true )
title() { [ -t 1 ] && clear; echo; echo "  ${B}$*${N}"; echo; }
say()   { echo "  $*"; }
step()  { echo; echo "  ${B}> $*${N}"; }
pause() { [ "${NO_PAUSE:-0}" = 1 ] && return; echo; read -r -p "  [Enter]" _; }
json()  { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }   # json 'expr on d'
