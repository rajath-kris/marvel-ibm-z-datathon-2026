# Optional NNPA scoring

**Status: optional, off by default, measured.** It runs on the IBM Z NNPA accelerator and matches the CPU path within the agreed
limit. It is **not faster** for this model, and the accelerator uses reduced precision. Do not claim a speed-up.

## What it is
`hospital/nnpa_scoring.py`, switched on with `NNPA_SCORE=1` in the hospital container. The fedsepsis library scores rows as
`z @ w` (standardised rows times the weight vector, float64). With the switch on, only that product runs on the accelerator
(torch device `nnpa`, float32 in, shape (rows, 138) x (138, 1)). Standardising, clipping, the sigmoid, the alert rule and the
threshold search are the library's own code. `lib/fedsepsis` is not modified and training (`local_train`) never uses the accelerator
(the swap is per thread and only while a scoring function runs).

| Env var | Default | Meaning |
|---|---|---|
| `NNPA_SCORE` | `0` | `1` turns the path on |
| `NNPA_SCOPE` | `patient` | `patient`: `/score` and `/patient`. `all`: also `/audit` and `/retune` |
| `NNPA_MIN_ROWS` | `0` | blocks with fewer rows stay on the CPU |

Safety: if torch or the device is missing, the start-up self-test fails, or any call errors or returns a non-finite value, the CPU
product is used (`event=nnpa_fallback` in the log, counted in `/health` -> `nnpa.fallbacks`). The service never fails because of it.
Log lines: `event=nnpa_enabled`, then `event=nnpa_scored fn=... scored_on=nnpa|cpu nnpa_rows=N` per scoring call.
`/health` has an `nnpa` field (`null` when off).

## Files
| File | Purpose |
|---|---|
| `hospital/nnpa_scoring.py` | the module |
| `hospital/client.py` | +12 lines: import and `install()` when `NNPA_SCORE=1`; `nnpa` field in `/health` (backup: `client.py.bak-*`) |
| `hospital/Dockerfile.nnpa` | `hospital/Dockerfile` plus one `COPY` of the module. Image tag `fedsepsis-hospital:nnpa` |
| `tests/test_nnpa_parity.py` | CPU vs NNPA on the real demo data |
| `evidence-hardening/nnpa_parity_forward.txt`, `..._reverse.txt` | raw results |

To ship it in the normal image, add `COPY hospital/nnpa_scoring.py /app/nnpa_scoring.py` to `hospital/Dockerfile` (the one line that
`Dockerfile.nnpa` adds). Without it, `NNPA_SCORE=1` logs `nnpa_fallback` (module missing) and the CPU path is used. The live containers
were NOT rebuilt or restarted for this work; the new image is a separate tag.

## Results (hardened container: non-root, read-only root, caps dropped; separate test container)
Acceptance rule, fixed before running: difference in alerts per 100 patient-days between CPU and NNPA below 0.1.

| | forward (B small, A's model) | reverse (A small, B's model) |
|---|---|---|
| CPU path reproduces the live number | 11.666600 and 23.995858 | 93.547483 and 27.157763 |
| NNPA path, stored threshold | 11.698459 (diff 0.032) | 93.633115 (diff 0.086) |
| NNPA path, re-tuned threshold on test | 24.021345 (diff 0.026) | 27.182229 (diff 0.024) |
| max / mean score difference | 2.0e-3 / 1.5e-4 | 1.9e-3 / 2.0e-4 |
| per-patient alert sequences that differ (first 2000 patients) | 2 | 17 |
| time to score all test rows, CPU / NNPA | 0.20 s / 0.24 s | 0.22 s / 0.26 s |
| result | PASS | PASS |

The reverse direction is close to the limit (0.086 of 0.1) because its stored threshold is low and many scores sit near it.

## Not tested
- End to end through the HTTP endpoints of a running hospital container (the library-level parity test and an import smoke test of the
  `client.py` hook were run; the running stacks were not touched).
- Training on the accelerator (deliberately not done: it would change the exact-match guarantee).
- Larger or different models, where the accelerator may pay off.
