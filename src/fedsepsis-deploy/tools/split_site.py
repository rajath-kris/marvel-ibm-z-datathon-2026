"""Split one site's training npz into two sites by PATIENT (never splitting a patient's rows).

    python split_site.py IN.npz OUT_DIR NAME_A NAME_B                  # random 50/50 split, seed 2026
    python split_site.py IN.npz OUT_DIR NAME_A NAME_B --map map.json   # {"<patient_id>": NAME_A|NAME_B, ...}

For the 3-site secure-aggregation demo: hospital A is split into two simulated sites (a SIMULATION, label it so).
The --map form takes Reju's patient-to-ICU map (medical / surgical) once it arrives.
Writes OUT_DIR/NAME_A.npz and OUT_DIR/NAME_B.npz with the same keys, loaded/saved with allow_pickle=False.
"""
import argparse
import json
import os

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("out_dir")
ap.add_argument("name_a")
ap.add_argument("name_b")
ap.add_argument("--map", default=None, help="json {patient_id: name_a|name_b}")
ap.add_argument("--seed", type=int, default=2026)
ap.add_argument("--drop-unmapped", action="store_true", help="leave out patients that are not in the map (unknown unit)")
a = ap.parse_args()

with np.load(a.src, allow_pickle=False) as z:
    d = {k: z[k] for k in z.files}
pid = d["patient_id"]
patients = np.unique(pid)
if a.map:
    with open(a.map) as f:
        m = {str(k): v for k, v in json.load(f).items()}
    unknown = [p for p in patients.tolist() if str(p) not in m]
    if unknown and not a.drop_unmapped:
        raise SystemExit(f"{len(unknown)} patients missing from the map, e.g. {unknown[:5]} (use --drop-unmapped)")
    bad = sorted({v for v in m.values()} - {a.name_a, a.name_b})
    if bad:
        raise SystemExit(f"map values must be {a.name_a} or {a.name_b}, found {bad}")
    to_a = np.array([m.get(str(p)) == a.name_a for p in patients.tolist()])
    to_b = np.array([m.get(str(p)) == a.name_b for p in patients.tolist()])
    how = f"map {os.path.basename(a.map)}" + (f", {len(unknown)} unmapped patients left out" if unknown else "")
else:
    rng = np.random.default_rng(a.seed)
    to_a = np.zeros(len(patients), dtype=bool)
    to_a[rng.permutation(len(patients))[: len(patients) // 2]] = True
    to_b = ~to_a
    how = f"random 50/50 by patient, seed {a.seed}"
row_a = np.isin(pid, patients[to_a])
row_b = np.isin(pid, patients[to_b])
os.makedirs(a.out_dir, exist_ok=True)
counts = {"how": how, "source_patients": int(len(patients)), "left_out_patients": int(len(patients) - to_a.sum() - to_b.sum())}
for name, rows in ((a.name_a, row_a), (a.name_b, row_b)):
    part = {k: (v if k == "feature_names" else v[rows]) for k, v in d.items()}
    np.savez(os.path.join(a.out_dir, name + ".npz"), **part)
    counts[name] = {"rows": int(rows.sum()), "patients": int(len(np.unique(pid[rows]))), "positive_rows": int(part["y"].sum())}
    print(f"{name}: {int(rows.sum())} rows, {len(np.unique(pid[rows]))} patients, "
          f"{int(part['y'].sum())} positive rows -> {name}.npz")
with open(os.path.join(a.out_dir, "split_counts.json"), "w") as f:
    json.dump(counts, f, indent=1)
print(f"split: {how}; source {os.path.basename(a.src)} {len(pid)} rows, {len(patients)} patients, left out {counts['left_out_patients']}")
