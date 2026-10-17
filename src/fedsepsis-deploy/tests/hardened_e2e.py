#!/usr/bin/env python3
"""End-to-end checks for the hardened test stack: the same checks as tests/test_real_endpoints.sh (training round, /audit, /retune,
/patient, /score, CORS, allowed-fields) with the numbers asserted against the experiment (expected_experiment.json in the handoff)
and compared with the LIVE deployment's saved evidence (read-only). Standard library only.

    RUN_DIR=$PWD/run/hardened python3 tests/hardened_e2e.py TAG

Writes evidence-hardening/e2e_TAG.json (measured numbers). Exit code 1 if any check fails.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

TAG = sys.argv[1] if len(sys.argv) > 1 else "plain"
ROOT = Path(__file__).resolve().parent.parent
REAL_DIR = Path(os.environ.get("REAL_DIR", Path.home() / "handoff/outputs/demo"))
RUN_DIR = Path(os.environ.get("RUN_DIR", ROOT / "run/hardened"))
AGG = os.environ.get("AGG_NAME", "hard-aggregator")
HOSPA = os.environ.get("HOSPA_NAME", "hard-hospA")
B = os.environ.get("B_URL", "http://127.0.0.1:18102")
LIVE_EVIDENCE = Path.home() / "fedsepsis-deploy/evidence/test_real_endpoints.txt"      # read-only comparison

RESULTS, MEASURED = [], {}


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)


def http(method, path, body=None, headers=None, base=B):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}"), dict(e.headers)
        except ValueError:
            return e.code, {}, dict(e.headers)


def hospa(method, path, body=None):
    """hospA is not published on the host: call it from inside its own container."""
    cmd = ["docker", "exec", HOSPA, "curl", "-s", "-m", "900", "-w", "\n%{http_code}", "-X", method]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd + ["http://127.0.0.1:8100" + path], capture_output=True, text=True).stdout
    txt, _, code = out.rpartition("\n")
    try:
        return int(code), json.loads(txt or "{}")
    except ValueError:
        return int(code or 0), {}


exp = json.loads((REAL_DIR / "expected_experiment.json").read_text())["setups"]
S1, S2, S4 = (exp[k]["budgets"]["25"] for k in ("1", "2", "4"))
dp = json.loads((REAL_DIR / "demo_patient.json").read_text())
state = json.loads((RUN_DIR / "state" / "state.json").read_text())
rounds = state["expected_rounds"]
full = rounds == json.loads((REAL_DIR / "repro_config.json").read_text())["setup4"]["rounds"]
print(f"stack: tag={TAG} rounds={rounds} ({'full repro_config.json run' if full else 'SHORT run: federated numbers are not compared'})")

# ---- 1. the federated training round ---------------------------------------------------------------------
check("training: aggregator done, all rounds completed", state["done"] and state["round"] == rounds, f"round {state['round']}/{rounds}")
fm = {s: json.loads((RUN_DIR / "out" / s / "final_model.json").read_text())["model"] for s in ("hospA", "hospB")}
check("training: both hospitals hold the identical final model", fm["hospA"]["w"] == fm["hospB"]["w"] and fm["hospA"]["b"] == fm["hospB"]["b"])
st, h, _ = http("GET", "/health")
check("hospB /health: done, final model, real mode", st == 200 and h.get("status") == "done" and h.get("final_model") is True and h.get("mode") == "real", str(h.get("status")))

# ---- 2. input validation -------------------------------------------------------------------------------------
for label, m, p, body, want in (("audit unknown model", "POST", "/audit", {"model": "bogus"}, 400),
                                ("audit threshold out of range", "POST", "/audit", {"model": "borrowed", "threshold": 2}, 400),
                                ("retune bad budget", "POST", "/retune", {"model": "borrowed", "budget": -5}, 400),
                                ("retune model=retuned not allowed", "POST", "/retune", {"model": "retuned", "budget": 25}, 400),
                                ("score without body fields", "POST", "/score", {}, 400)):
    st, _, _ = http(m, p, body)
    check(f"validation: {label} -> {want}", st == want, f"HTTP {st}")

# ---- 3. the numbers that must match the live deployment ---------------------------------------------------------------
st, a1, _ = http("POST", "/audit", {})
r1 = a1["alerts_per_100_patient_days"]
MEASURED["borrowed_rate_stored_threshold"] = r1
check("audit borrowed (stored threshold): 11.67 per 100 patient-days", st == 200 and abs(r1 - S1["realized_rate_test"]) < 1e-6,
      f"{r1:.6f} vs experiment {S1['realized_rate_test']:.6f}")
thr2 = 0.425315265515
st, a2, _ = http("POST", "/audit", {"model": "borrowed", "threshold": thr2})
r2 = a2["alerts_per_100_patient_days"]
MEASURED["borrowed_rate_at_setup2_threshold"] = r2
check("audit borrowed at the setup-2 threshold: 24.0", abs(r2 - S2["realized_rate_test"]) < 1e-6, f"{r2:.6f} vs experiment {S2['realized_rate_test']:.6f}")
st, t1, _ = http("POST", "/retune", {"model": "borrowed", "budget": 25})
MEASURED["retune_borrowed_threshold"] = t1.get("threshold")
check("retune borrowed to budget 25: threshold equals setup 2's", st == 200 and abs(t1["threshold"] - S2["threshold"]) < 1e-9,
      f"{t1.get('threshold')} vs {S2['threshold']}")
st, a3, _ = http("POST", "/audit", {"model": "retuned"})
r3 = a3["alerts_per_100_patient_days"]
MEASURED["retuned_rate"] = r3
check("audit retuned (borrowed model): 24.0", abs(r3 - S2["realized_rate_test"]) < 1e-6, f"{r3:.6f}")
st, a4, _ = http("POST", "/audit", {"model": "federated"})
MEASURED["federated_rate_stored_threshold"] = a4.get("alerts_per_100_patient_days")
if full:
    check("audit federated (stored threshold) equals setup 4 of the experiment", abs(a4["alerts_per_100_patient_days"] - S4["realized_rate_test"]) < 1e-3,
          f"{a4['alerts_per_100_patient_days']:.6f} vs {S4['realized_rate_test']:.6f}")
    st, t2, _ = http("POST", "/retune", {"model": "federated", "budget": 25})
    check("retune federated to budget 25: threshold equals setup 4's", abs(t2["threshold"] - S4["threshold"]) < 1e-5, f"{t2['threshold']:.9f} vs {S4['threshold']:.9f}")
    st, a5, _ = http("POST", "/audit", {"model": "retuned"})
    check("audit retuned federated equals setup 4 of the experiment", abs(a5["alerts_per_100_patient_days"] - S4["realized_rate_test"]) < 1e-3,
          f"{a5['alerts_per_100_patient_days']:.6f}")
else:
    print(f"INFO  federated /audit with a {rounds}-round model: {MEASURED['federated_rate_stored_threshold']} (not compared with the 40-round experiment)")

# ---- 4. same numbers as the LIVE deployment's saved evidence (read-only file) ----------------------------------------------
if LIVE_EVIDENCE.exists():
    vals = [float(v) for v in re.findall(r'"alerts_per_100_patient_days": ([0-9.]+)', LIVE_EVIDENCE.read_text())]
    if len(vals) >= 2:
        check("live deployment (evidence/test_real_endpoints.txt): borrowed rate identical", abs(vals[0] - r1) < 1e-6, f"live {vals[0]:.6f} vs test stack {r1:.6f}")
        check("live deployment: borrowed at the setup-2 threshold identical", abs(vals[1] - r2) < 1e-6, f"live {vals[1]:.6f} vs test stack {r2:.6f}")
    else:
        print("INFO  live evidence file has fewer than 2 audit numbers; live comparison skipped")
else:
    print("INFO  live evidence file not found; live comparison skipped")

# ---- 5. replay and score ---------------------------------------------------------------------------------------------------------
for kind in ("success_example", "failure_example"):
    ex = dp[kind]
    for m, ref in (("borrowed", "asis"), ("federated", "federated")):
        if m == "federated" and not full:
            continue
        st, rp, _ = http("GET", f"/patient?patient_id={ex['patient_id']}&model={m}")
        n = len(ex["hours"])
        d = max(abs(x - y) for x, y in zip(rp["score"][:n], ex["score_" + ref])) if st == 200 else float("nan")
        same = st == 200 and rp["alert"][:n] == ex["alert_" + ref]
        check(f"replay {kind} patient {ex['patient_id']} {m}: scores and alerts match demo_patient.json", st == 200 and d < 1e-6 and same, f"max |diff| {d:.1e}, alerts identical {same}")
st, _, _ = http("GET", "/patient?patient_id=999999&model=borrowed")
check("replay unknown patient -> 404", st == 404, f"HTTP {st}")
st, sc, _ = http("POST", "/score", {"patient_id": dp["success_example"]["patient_id"], "hour": 29, "model": "borrowed"})
check("score patient 71 hour 29 (borrowed) returns hour, score, alert", st == 200 and sc.get("hour") == 29 and "score" in sc and "alert" in sc, f"HTTP {st}")

# ---- 6. CORS ------------------------------------------------------------------------------------------------------------------------
_, _, hd = http("GET", "/health", headers={"Origin": "http://localhost:5173"})
check("CORS: http://localhost origin allowed", hd.get("Access-Control-Allow-Origin") == "http://localhost:5173")
_, _, hd = http("GET", "/health", headers={"Origin": "http://evil.example"})
check("CORS: foreign origin gets no Access-Control-Allow-Origin", "Access-Control-Allow-Origin" not in hd)

# ---- 7. allowed fields: only aggregates leave the hospital ----------------------------------------------------------
log = subprocess.run(["docker", "logs", AGG], capture_output=True, text=True)
keys = set()
for line in (log.stdout + log.stderr).splitlines():
    m = re.search(r"event=request_keys .* keys=(\S*)", line)
    if m:
        keys |= set(filter(None, m.group(1).split(",")))
allowed = {"site_id", "n", "sum", "sumsq", "round", "w", "b"}
check("allowed fields: request keys at the aggregator are a subset of the allowed set", bool(keys) and keys <= allowed, f"seen {sorted(keys)}")

# ---- 8. hospA (the big site) through its own container --------------------------------------------------------------------------------
st, ha = hospa("GET", "/health")
check("hospA /health (inside its container) ok", st == 200 and ha.get("ok") is True and ha.get("final_model") is True)
st, ra = hospa("POST", "/audit", {})
MEASURED["hospA_borrowed_rate_on_its_training_rows"] = ra.get("alerts_per_100_patient_days")
check("hospA /audit borrowed on its own rows answers", st == 200 and isinstance(ra.get("alerts_per_100_patient_days"), float), f"{ra.get('alerts_per_100_patient_days')}")

n_fail = RESULTS.count(False)
print(f"\n================ E2E {TAG}: {RESULTS.count(True)} passed, {n_fail} failed ================")
out = ROOT / "evidence-hardening"
out.mkdir(exist_ok=True)
(out / f"e2e_{TAG}.json").write_text(json.dumps({"tag": TAG, "rounds": rounds, "passed": RESULTS.count(True), "failed": n_fail, "measured": MEASURED}, indent=1))
sys.exit(1 if n_fail else 0)
