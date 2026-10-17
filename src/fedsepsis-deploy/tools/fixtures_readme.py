"""Write demo_fixtures/README.txt: one line per file (endpoint, capture time, deployment version)."""
import json
import os
import sys

log, d, dep = sys.argv[1], sys.argv[2], sys.argv[3]
lines = ["SepsisBridge demo fixtures: exact JSON responses recorded from the live deployment (no token, no seeds, no patient rows).",
         "Hospital B (small hospital): /audit and /patient use small_test_patients.npz (9,885 held-out patients); /retune uses small_val_patients.npz.",
         "Captured in the order below: retune_borrowed sets the stored 'retuned' threshold used by audit_retuned and patient_*_retuned.",
         ""]
for row in open(log):
    f, call, ts, code = row.strip().split("|")
    lines.append(f"{f}  |  {call}  |  captured {ts}  |  HTTP {code}  |  {dep}")
s = json.load(open(os.path.join(d, "secure_summary.json")))
lines.append(f"secure_summary.json  |  not an HTTP endpoint: 3-site secure aggregation run (tests/test_secure_e2e.sh, SECURE=1 vs SECURE=0)  |  "
             f"generated {s['generated_utc']}  |  -  |  sec-* containers on the same VM, code commit 1e048b3, fedsepsis 0.2.0, "
             f"{s['rounds']} rounds, sites {', '.join(s['sites'])} (hospA1/hospA2 = simulated split of hospital A)")
with open(os.path.join(d, "README.txt"), "w") as fh:
    fh.write("\n".join(lines) + "\n")
print("\n".join(lines))
