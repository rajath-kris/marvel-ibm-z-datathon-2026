"""Compare two model JSON files (bare model or our final_model.json wrapper). Standard library only.
Usage: python3 tools/compare_models.py OURS.json REFERENCE.json"""
import json
import sys


def load(p):
    with open(p) as f:
        m = json.load(f)
    return m.get("model", m)


a, b = load(sys.argv[1]), load(sys.argv[2])
print(f"ours      : {sys.argv[1]}")
print(f"reference : {sys.argv[2]}")
print(f"weights   : {len(a['w'])} vs {len(b['w'])}")
print(f"feature_names identical (same order): {a.get('feature_names') == b.get('feature_names')}")
if len(a["w"]) != len(b["w"]):
    sys.exit("different number of weights")
for key in ("w", "mean", "std"):
    d = [abs(x - y) for x, y in zip(a[key], b[key])]
    i = max(range(len(d)), key=d.__getitem__)
    print(f"max |{key:4s} diff| = {d[i]:.3e}   (at {a.get('feature_names', [None] * len(d))[i] or i}, ref value {b[key][i]:.6g})")
print(f"|b diff|        = {abs(a['b'] - b['b']):.3e}   (ours {a['b']:.9f}, ref {b['b']:.9f})")
scale = max(abs(x) for x in b["w"])
print(f"max |w diff| relative to max |w_ref| ({scale:.4f}): {max(abs(x - y) for x, y in zip(a['w'], b['w'])) / scale:.3e}")
