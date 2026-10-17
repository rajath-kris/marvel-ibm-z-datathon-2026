"""Build demo/secure_summary.json from a masked + a plain 3-site run. Standard library only.
Contains only: site names, row counts n (sent in the clear anyway), masked values the aggregator received,
first model weights, differences, round count and runtimes. No tokens, no seeds, no patient data.
Usage: python3 tools/secure_summary.py MASKED_RESULTS_DIR PLAIN_RESULTS_DIR SECURE_SEEN_JSON SPLIT_TAG OUT_JSON"""
import json
import sys
from datetime import datetime, timezone

masked_dir, plain_dir, seen_path, split_tag, out = sys.argv[1:]
load = lambda p: json.load(open(p))
ms, ps = load(f"{masked_dir}/final_model.json"), load(f"{plain_dir}/final_model.json")
mi, pi = load(f"{masked_dir}/run_info.json"), load(f"{plain_dir}/run_info.json")
seen = load(seen_path)
m, p = ms["model"], ps["model"]
diff = max([abs(a - b) for a, b in zip(m["w"], p["w"])] + [abs(m["b"] - p["b"])])
sites = sorted(seen)
assert sorted(sites) == sorted(["hospA1", "hospA2", "hospB"]), sites
summary = {
    "title": "SepsisBridge: 3-site secure aggregation (pairwise masks) vs plain FedAvg",
    "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "sites": sites,
    "simulation_note": ("hospA1 and hospA2 are hospital A split in two by patient (" + split_tag +
                        "): a simulation of two sites; hospB is the small hospital."),
    "rounds": int(mi["rounds"]),
    "local_epochs": ms.get("train_cfg", {}).get("epochs"),
    "what_the_aggregator_saw": {
        "round": max(v["round"] for v in seen.values()),
        "description": ("first 3 of 138 masked n*w values each site sent in that round. Integers mod 2**128, given as "
                        "decimal STRINGS because they exceed JavaScript's safe integer range; *_fraction is value / 2**128 "
                        "for display. On its own each value is uniform random; the masks cancel only in the 3-site sum."),
        "per_site": {s: {"n_rows_sent_in_clear": v["n"], "masked_first3": v["w_first_masked"],
                         "masked_first3_fraction": [int(x) / 2 ** 128 for x in v["w_first_masked"]]}
                     for s, v in sorted(seen.items())},
    },
    "final_model_first3": {
        "feature_names": m["feature_names"][:3],
        "secure_run": m["w"][:3],
        "plain_run": p["w"][:3],
    },
    "final_bias": {"secure_run": m["b"], "plain_run": p["b"]},
    "max_abs_difference_secure_vs_plain": diff,
    "max_abs_difference_scope": "all 138 weights and the bias of the final model",
    "runtime_seconds": {"secure_run": mi["runtime_seconds"], "plain_run": pi["runtime_seconds"]},
}
json.dump(summary, open(out, "w"), indent=1)
print(f"wrote {out}: sites {sites}, max |secure - plain| {diff:.3e}, runtimes {mi['runtime_seconds']}s / {pi['runtime_seconds']}s")
