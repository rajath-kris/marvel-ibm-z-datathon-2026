"""Write demo_fixtures/README.txt: one line per file (endpoint, capture time, deployment version)."""
import json
import os
import sys

d = sys.argv[1]
lines = ["Dr Sepsis demo fixtures v2: exact JSON responses recorded from the live deployments on the LinuxONE VM",
         "(no token, no seeds, no feature rows; patient files hold one model score or NEWS2 score per hour).",
         "Files with no suffix: original direction, hospital B small, borrowing hospital A's model (outputs/demo/).",
         "Suffix _reverse: hospital A small, borrowing hospital B's model (outputs/demo_reverse/).",
         "Suffix _news2 / _news2_reverse: the NEWS2 baseline (partial NEWS2, alert at score >= 7) on the same test patients.",
         "Per direction the calls were made in the order listed: retune_borrowed sets the stored 'retuned' threshold used by",
         "audit_retuned and patient_*_retuned. retune_news2 does not search (no NEWS2 scores for validation patients): it",
         "returns the experiment's threshold with note 'fixed from experiment'. Columns: file | call | captured (UTC) | HTTP | deployment",
         ""]
for row in open(os.path.join(d, ".capture.log")):
    f, call, ts, code, dep = row.rstrip("\n").split("|")
    lines.append(f"{f}  |  {call}  |  {ts}  |  {code}  |  {dep}")
s = json.load(open(os.path.join(d, "secure_summary.json")))
lines.append(f"secure_summary.json  |  not an HTTP call: 3-site secure aggregation run (tests/test_secure_e2e.sh, SECURE=1 vs SECURE=0)  |  "
             f"{s['generated_utc']}  |  -  |  {s.get('deployment', 'sec-* containers on the same VM')}")
with open(os.path.join(d, "README.txt"), "w") as fh:
    fh.write("\n".join(lines) + "\n")
print(f"README.txt: {len(lines) - 9} entries")
