"""CPU vs NNPA scoring on the real demo data (forward bundle: hospital A model, hospital B small test/val patients).

Uses the real fedsepsis library (mounted at /app/lib) and nnpa_scoring.install(). Nothing here changes any service.
Reports: max/mean score difference, alert rate and alert-decision differences at the experiment's stored threshold,
the re-tuned threshold from each path, and wall-clock time per scoring pass (median of 3, incl. transfer).
Env: DATA=/data (bundle folder), LIB=/app/lib, NNPA_SCOPE (patient|all) is irrelevant here: the test patches `_patient_scores`
itself by installing with NNPA_SCOPE=all.
"""
import json
import os
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, os.environ.get("LIB", "/app/lib"))
sys.path.insert(0, "/app")
os.environ["NNPA_SCOPE"] = "all"
import fedsepsis as fs                      # noqa: E402
import nnpa_scoring                         # noqa: E402

D = os.environ.get("DATA", "/data")
THR_BORROWED = float(os.environ.get("THR_BORROWED", "0.5168503567531072"))   # expected_experiment.json setup 1, budget 25
EXPECT_RATE = float(os.environ.get("EXPECT_RATE", "11.66660029469157"))


def patients(path):
    with np.load(path, allow_pickle=False) as z:
        X, pid, hour = z["X"], z["patient_id"], z["hour"]
    o = np.lexsort((hour, pid))
    if not np.array_equal(o, np.arange(len(pid))):
        X, pid = X[o], pid[o]
    cuts = np.flatnonzero(pid[1:] != pid[:-1]) + 1
    s, e = np.r_[0, cuts].tolist(), np.r_[cuts, len(pid)].tolist()
    return [X[a:b] for a, b in zip(s, e)], len(pid)


def model(name):
    m = json.load(open(os.path.join(D, name)))
    return m.get("model", m)


def timed(f, reps=3):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        out = f()
        ts.append(time.perf_counter() - t0)
    return out, statistics.median(ts)


test_p, n_test = patients(os.path.join(D, "small_test_patients.npz"))
val_p, n_val = patients(os.path.join(D, "small_val_patients.npz"))
print("data: test %d patients / %d rows; val %d patients / %d rows" % (len(test_p), n_test, len(val_p), n_val))

orig = {k: getattr(fs, k) for k in ("_scores", "_patient_scores", "_std_chunk")}
events = []
ok = nnpa_scoring.install(fs, lambda ev, **kw: events.append((ev, kw)) if ev != "nnpa_scored" else None)
print("install ->", ok, [e for e in events if e[0] in ("nnpa_enabled", "nnpa_fallback")])
if not ok:
    sys.exit("NNPA not usable here")
nn = {k: getattr(fs, k) for k in orig}


def use(which):
    for k, v in (orig if which == "cpu" else nn).items():
        setattr(fs, k, v)


bad = 0
for mname, thr in (("model_big_asis.json", THR_BORROWED),):
    m = model(mname)
    res = {}
    for which in ("cpu", "nnpa"):
        use(which)
        (sc, _), secs = timed(lambda: fs._patient_scores(m, test_p))
        rate = fs.alert_rate(m, test_p, thr)
        (rt, rsecs) = timed(lambda: fs.threshold_for_budget(m, val_p, 25.0), reps=1)
        retuned_on_test = fs.alert_rate(m, test_p, rt["threshold"])
        res[which] = dict(scores=np.concatenate(sc), secs=secs, rate=rate, rt=rt, rt_secs=rsecs, rt_test=retuned_on_test)
    c, n = res["cpu"], res["nnpa"]
    d = np.abs(c["scores"] - n["scores"])
    print("\n== %s (borrowed model, threshold %.6f)" % (mname, thr))
    print("scores: max |diff| %.2e, mean |diff| %.2e, rows with |diff|>1e-3: %d of %d" % (d.max(), d.mean(), int((d > 1e-3).sum()), len(d)))
    print("alert rate on test @ stored threshold: cpu %.6f (expected %.6f) | nnpa %.6f | alerts cpu %d nnpa %d"
          % (c["rate"]["alerts_per_100pd"], EXPECT_RATE, n["rate"]["alerts_per_100pd"], c["rate"]["alerts"], n["rate"]["alerts"]))
    print("retuned threshold (budget 25, val): cpu %.6f | nnpa %.6f | realized on val: cpu %.4f nnpa %.4f"
          % (c["rt"]["threshold"], n["rt"]["threshold"], c["rt"]["realized_per_100pd"], n["rt"]["realized_per_100pd"]))
    print("retuned model on test: cpu %.6f | nnpa %.6f per 100 patient-days" % (c["rt_test"]["alerts_per_100pd"], n["rt_test"]["alerts_per_100pd"]))
    print("time, scoring all test rows: cpu %.3fs | nnpa %.3fs (median of 3)  | retune: cpu %.2fs nnpa %.2fs" % (c["secs"], n["secs"], c["rt_secs"], n["rt_secs"]))
    diff_rate = abs(c["rate"]["alerts_per_100pd"] - n["rate"]["alerts_per_100pd"])
    diff_rt = abs(c["rt_test"]["alerts_per_100pd"] - n["rt_test"]["alerts_per_100pd"])
    print("PARITY: |rate diff| %.4f, |retuned-on-test rate diff| %.4f (limit 0.1 alerts per 100 patient-days)" % (diff_rate, diff_rt))
    bad += (diff_rate > 0.1) + (diff_rt > 0.1)

# per-patient decisions (what /score and /patient show): same alerts hour by hour?
use("nnpa")
flips = 0
for p in test_p[:2000]:
    a = [r["alert"] for r in fs.score_patient(model("model_big_asis.json"), p, THR_BORROWED)]
    use("cpu")
    b = [r["alert"] for r in fs.score_patient(model("model_big_asis.json"), p, THR_BORROWED)]
    use("nnpa")
    flips += a != b
print("\nper-patient alert sequences (first 2000 test patients) that differ cpu vs nnpa:", flips)
print("nnpa_scoring.stats():", nnpa_scoring.stats())
print("RESULT:", "PASS" if bad == 0 else "FAIL (rate difference above the 0.1 limit)")
sys.exit(1 if bad else 0)
