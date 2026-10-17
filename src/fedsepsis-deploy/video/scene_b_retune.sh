#!/usr/bin/env bash
# Scene b: hospital A (small) borrows hospital B's model. Audit, re-tune, audit again.
source "$(dirname "$0")/common.sh"
URL=${URL:-http://127.0.0.1:8202}      # reverse direction: small hospital = hospital A
ward() { python3 -c "print(f'{$1 * 0.2:.1f}')"; }   # 20 beds = 20 patient-days per day
title "Small hospital borrows a big hospital's model"
say "Target: 25 alerts per 100 patient-days"
say "        = $(ward 25) alerts a day on a 20-bed ward"
pause
step "1. Audit the borrowed model (its own threshold)"
r=$(curl -s -X POST -d '{"model":"borrowed"}' "$URL/audit")
rate=$(echo "$r" | json 'd["alerts_per_100_patient_days"]')
say "$(printf '%.1f alerts per 100 patient-days' "$rate")"
say "= $(ward "$rate") alerts a day on a 20-bed ward"
old_thr=$(echo "$r" | json 'round(d["threshold"], 4)')
say "($(echo "$r" | json 'format(d["patients"], ",")') patients, threshold $old_thr, no labels used)"
pause
step "2. Re-tune the threshold locally (budget 25)"
r=$(curl -s -X POST -d '{"model":"borrowed","budget":25}' "$URL/retune")
say "new threshold $(echo "$r" | json 'round(d["threshold"], 4)') (was $old_thr)"
pause
step "3. Audit again"
r=$(curl -s -X POST -d '{"model":"retuned"}' "$URL/audit")
rate=$(echo "$r" | json 'd["alerts_per_100_patient_days"]')
say "$(printf '%.1f alerts per 100 patient-days' "$rate")"
say "= $(ward "$rate") alerts a day on a 20-bed ward"
echo
