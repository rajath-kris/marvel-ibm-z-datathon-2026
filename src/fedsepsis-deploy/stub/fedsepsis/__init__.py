"""STUB fedsepsis package: pure Python, no numpy.

Same frozen function names and return shapes as the real library, so the
deployment can be tested before the real library exists. Nothing here is a
real model: local_train just perturbs the global weights with seeded noise.
"""
import math
import random

IS_STUB = True


# ---- extra helper (stub only): synthetic data ---------------------------
def generate_dataset(seed, n_rows=2000, n_features=20, hours_per_patient=40, site_prefix="P"):
    """Seeded synthetic dataset with the same keys as the real .npz format."""
    rng = random.Random(seed)
    shift = rng.uniform(-0.5, 0.5)
    X, y, pid, hour = [], [], [], []
    for i in range(n_rows):
        p, h = divmod(i, hours_per_patient)
        row = [rng.gauss(shift + 0.1 * j, 1.0 + 0.05 * j) for j in range(n_features)]
        X.append(row)
        y.append(1 if rng.random() < 0.08 else 0)
        pid.append(f"{site_prefix}-{p:04d}")
        hour.append(h)
    return {"X": X, "y": y, "patient_id": pid, "hour": hour,
            "feature_names": [f"f{j:02d}" for j in range(n_features)]}


# ---- frozen contract ----------------------------------------------------
def local_stats(X):
    n = len(X)
    d = len(X[0])
    s = [0.0] * d
    sq = [0.0] * d
    for row in X:
        for j, v in enumerate(row):
            v = float(v)
            s[j] += v
            sq[j] += v * v
    return {"n": n, "sum": s, "sumsq": sq}


def combine_stats(stats_list):
    total = sum(s["n"] for s in stats_list)
    d = len(stats_list[0]["sum"])
    mean, std = [], []
    for j in range(d):
        m = sum(s["sum"][j] for s in stats_list) / total
        var = sum(s["sumsq"][j] for s in stats_list) / total - m * m
        mean.append(m)
        std.append(max(math.sqrt(max(var, 0.0)), 1e-8))
    return {"mean": mean, "std": std}


def local_train(model, X, y, cfg):
    seed = f"{cfg.get('seed', 0)}:{cfg.get('site_id', '')}:{cfg.get('round', 0)}"
    rng = random.Random(seed)
    scale = float(cfg.get("noise", 0.05))
    w = [float(v) + rng.gauss(0.0, scale) for v in model["w"]]
    b = float(model["b"]) + rng.gauss(0.0, scale)
    return {"w": w, "b": b, "n": len(X)}


def fedavg_aggregate(updates):
    total = sum(u["n"] for u in updates)
    d = len(updates[0]["w"])
    return {"w": [sum(u["w"][j] * u["n"] for u in updates) / total for j in range(d)],
            "b": sum(u["b"] * u["n"] for u in updates) / total}


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def score_patient(model, patient_rows, threshold, silence_h=6):
    """patient_rows: {"X": [[f]], "hour": [int]} sorted by hour.
    Alert when score >= threshold, then stay silent for silence_h hours."""
    mean, std, w, b = model["mean"], model["std"], model["w"], model["b"]
    out, last_alert = [], None
    for x, h in zip(patient_rows["X"], patient_rows["hour"]):
        z = b + sum(wj * (float(xj) - mj) / sj for wj, xj, mj, sj in zip(w, x, mean, std))
        s = _sigmoid(z)
        alert = s >= threshold and (last_alert is None or h - last_alert >= silence_h)
        if alert:
            last_alert = h
        out.append({"hour": int(h), "score": s, "alert": bool(alert)})
    return out
