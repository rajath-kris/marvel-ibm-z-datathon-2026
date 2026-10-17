"""Check a running direction's small-hospital endpoints against the experiment (standard library only).
Usage: python3 tests/check_direction.py http://127.0.0.1:8202 ~/handoff/outputs/demo_reverse
Expected values come from the bundle's expected_experiment.json and demo_patient.json (nothing hard-coded).
Leaves the stored 'retuned' threshold = borrowed re-tuned at 25 (the demo story)."""
import json
import os
import sys
import urllib.request

base, real = sys.argv[1].rstrip("/"), os.path.expanduser(sys.argv[2])
exp = json.load(open(os.path.join(real, "expected_experiment.json")))
dp = json.load(open(os.path.join(real, "demo_patient.json")))
S = exp["setups"]


def call(method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


rows, ok_all = [], True


def check(label, ours, ref, tol):
    global ok_all
    d = abs(ours - ref)
    ok = d <= tol
    ok_all &= ok
    rows.append(f"  {'PASS' if ok else 'FAIL'}  {label:58s} ours {ours:12.6f}   experiment {ref:12.6f}   |diff| {d:.2e}")


print(f"# {exp['source']}")
print(f"# endpoint {base}, health: {json.dumps(call('GET', '/health'))}")
a = call("POST", "/audit", {"model": "borrowed"})
e1 = S["1"]["budgets"]["25"]
check("borrowed: stored threshold", a["threshold"], e1["threshold"], 1e-12)
check("borrowed: alerts / 100 patient-days on test", a["alerts_per_100_patient_days"], e1["realized_rate_test"], 1e-9)
r = call("POST", "/retune", {"model": "borrowed", "budget": 25})
e2 = S["2"]["budgets"]["25"]
check(f"retune borrowed @25 on {r['data']}: threshold", r["threshold"], e2["threshold"], 1e-9)
a = call("POST", "/audit", {"model": "retuned"})
check("retuned borrowed: alerts / 100 patient-days on test", a["alerts_per_100_patient_days"], e2["realized_rate_test"], 1e-9)
a = call("POST", "/audit", {"model": "federated"})
e4 = S["4"]["budgets"]["25"]
check("federated (this deployment's model): stored threshold", a["threshold"], e4["threshold"], 1e-12)
check("federated: alerts / 100 patient-days on test", a["alerts_per_100_patient_days"], e4["realized_rate_test"], 1e-9)
a = call("POST", "/audit", {"model": "news2"})
en = S["NEWS2"]["budgets"]["25"]
check("news2: stored threshold (points)", a["threshold"], en["threshold"], 1e-12)
check("news2: alerts / 100 patient-days on test", a["alerts_per_100_patient_days"], en["realized_rate_test"], 1e-9)
rn = call("POST", "/retune", {"model": "news2", "budget": 25})
rows.append(f"  {'PASS' if rn.get('note') == 'fixed from experiment' else 'FAIL'}  news2 /retune returns the documented threshold: "
            f"{rn['threshold']} ({rn.get('note')}; {rn.get('rule')})")
print("\n".join(rows))
print(f"  patient-days on test: {a['patient_days']}  patients: {a['patients']}")

print("# replay of demo_patient.json examples (reference: model_federated_best.json / model_big_asis.json)")
for kind in ("success_example", "failure_example"):
    ex = dp[kind]
    n = len(ex["hours"])
    for m, ref in (("federated", "federated"), ("borrowed", "asis")):
        rp = call("GET", f"/patient?patient_id={ex['patient_id']}&model={m}")
        d = max(abs(x - y) for x, y in zip(rp["score"][:n], ex["score_" + ref]))
        same = rp["alert"][:n] == ex["alert_" + ref]
        ok_all &= same and d < 1e-6
        print(f"  {'PASS' if same and d < 1e-6 else 'FAIL'}  {kind:16s} patient {ex['patient_id']:>6} {m:9s} alerts identical {same}, "
              f"max |score diff| {d:.2e}, alert hours {rp['alert_hours']}, onset {rp['onset_hour']}")
    for m in ("retuned", "news2"):
        rp = call("GET", f"/patient?patient_id={ex['patient_id']}&model={m}")
        print(f"        {kind:16s} patient {ex['patient_id']:>6} {m:9s} threshold {rp['threshold']:.6g}, alert hours {rp['alert_hours']}")
print("ALL PASS" if ok_all else "SOME CHECKS FAILED")
sys.exit(0 if ok_all else 1)
