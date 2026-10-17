"""Write a seeded synthetic dataset for one site (stub mode only). Usage: make_stub_data.py SITE_ID OUT_PATH"""
import json
import os
import sys
import zlib

sys.path.insert(0, "/app/stub")
from fedsepsis import generate_dataset  # noqa: E402

site, out = sys.argv[1], sys.argv[2]
rows = int(os.environ.get("STUB_ROWS", "2000"))
feats = int(os.environ.get("STUB_FEATURES", "20"))
d = generate_dataset(seed=zlib.crc32(site.encode()), n_rows=rows, n_features=feats, site_prefix=site)
with open(out, "w") as f:
    json.dump(d, f)
print(f"{site}: wrote {rows} rows x {feats} features, {len(set(d['patient_id']))} patients -> {out}")
