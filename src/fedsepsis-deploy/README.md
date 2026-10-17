# fedsepsis-deploy

Deployment of the Dr Sepsis / FedSepsis federated-learning demo on the IBM LinuxONE VM `datathon-test` (s390x).
One **aggregator** and one container per **hospital** exchange JSON over a private docker network. Hospital-to-aggregator
traffic uses **mutual TLS 1.3**, every container is **hardened** (`HARDENING.md`), and only counts, sums and model weights
leave a hospital. It runs Reju's real `fedsepsis` library (`MODE=real`). A pure-Python stub (`MODE=stub`) remains for
tests that don't need data.

**New to this project or setting it up elsewhere?** Start with [`SETUP.md`](SETUP.md).

```
              docker network fedsepsis-net (bridge)
  ┌────────────┐   POST /stats, /update   ┌──────────────────────┐
  │ hospA      │ ───────────────────────► │ aggregator :8000     │  no host port
  │ /data (ro) │ ◄─────────────────────── │ /state (state.json)  │
  │ /out       │   GET /norm, /global     └──────────────────────┘
  └────────────┘                                     ▲
     127.0.0.1:8101 → :8100 /score                   │ same API
  ┌────────────┐                                     │
  │ hospB      │ ────────────────────────────────────┘
  └────────────┘   127.0.0.1:8102 → :8100 /score
```

Only aggregates cross the network: per-feature `n/sum/sumsq` once, then `w/b/n` per round. Patient rows stay in each
hospital's read-only `/data` mount. Test (f) checks this against the aggregator's key log.

## Current live deployment (2026-10-18)

| Stack | Containers | Demo port (plain HTTP, 127.0.0.1) | Data |
|---|---|---|---|
| forward: hospital B small, borrows A's model | `fedsepsis-aggregator`, `hospA`, `hospB` | 8101 / **8102** | `~/handoff/outputs/demo/` |
| reverse: hospital A small, borrows B's model | `rev-fedsepsis-aggregator`, `rev-hospB`, `rev-hospA` | 8201 / **8202** | `~/handoff/outputs/demo_reverse/` |
| secure 3-site demo (started on demand) | `sec-aggregator`, `sec-hospA1` (medical ICU), `sec-hospA2` (surgical ICU), `sec-hospB` | 8111-8113 | ICU-map split of hospital A + B |

```bash
./run_live.sh                         # (re)start both directions: HARDEN=1 MTLS=1, settings from each bundle's repro_config.json
./run_live.sh reverse                 # one direction
python3 tests/check_direction.py http://127.0.0.1:8102 ~/handoff/outputs/demo           # numbers vs the experiment
python3 tests/check_direction.py http://127.0.0.1:8202 ~/handoff/outputs/demo_reverse
SPLIT_MAP=~/handoff/icu_map.json ./tests/test_secure_e2e.sh                               # secure vs plain, 3 sites
video/scene_a_linuxone.sh   # ... scene_d_fields.sh: the video scenes (video/README.md)
~/rollback_pre_merge.sh               # roll the live stacks back to before the hardening merge (tag pre-hardening-merge)
```

Verified on 2026-10-18 after the hardening merge (`evidence/check_forward.txt`, `evidence/check_reverse.txt`,
`evidence/hardening_flags.txt`):
- forward: borrowed 11.666600, re-tuned 23.995858, federated 22.453905, NEWS2 24.531082
- reverse: borrowed 93.547483, re-tuned 27.157763, federated 25.188213, NEWS2 30.729865

All match the experiment exactly. All six containers run as `1000:1000` with a read-only root filesystem, `CapDrop [ALL]`
and `no-new-privileges`. Both aggregators require client certificates (TLS 1.3). The API for the demo page is in
`DEMO_API.md` and is unchanged by the hardening.

## Base image decision (Step 1)

| | Result on this VM |
|---|---|
| `python:3.12-slim` | **not pulled**. The addendum said to skip it unless the Jupyter image failed, and it didn't |
| Jupyter image `registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest` | Python 3.12.15 at `/opt/venv/bin/python`, numpy 2.0.2, pandas 3.0.6, scikit-learn 1.9.0, `sys.byteorder == 'big'` |
| Container user | uid/gid 1000 (`ibm-user`), the same as host `linux1`. Bind-mount writes are owned by `linux1` (verified) |
| `docker compose version` | v5.5.1 is present, but plain `docker run` scripts are used for exact control of limits and the restart test |

**Decision:** both images are `FROM` the Jupyter image with the entrypoint overridden to `/opt/venv/bin/python -I`.
- The hospitals need numpy for the real library, and pip can't install it on this host.
- The aggregator only uses the standard library, but it runs on the same image so there is one base to trust.

Every container runs with `--memory 1g --cpus 1`, because the VM is shared (3 vCPUs).

The machine is big-endian, so only JSON (plain floats and lists) crosses the network, never binary arrays.

## Layout

| Path | What |
|---|---|
| `aggregator/aggregator.py` | Aggregator, standard library only. Logic is in `AggregatorState` (unit-tested), HTTP is in `make_handler` |
| `hospital/client.py` | Hospital client: the training loop runs in the main thread and the `/health` + `/score` server in a second thread |
| `stub/fedsepsis/` | Stub library with the frozen function names, plus `generate_dataset` for synthetic data |
| `hospital/make_stub_data.py` | Writes seeded synthetic data per site (hospA 2000 rows, hospB 1200 rows, 20 features) |
| `lib/` | **Empty.** Put the real `fedsepsis/` package here |
| `data/` | **Create it** and put `site_big.npz` and `site_small.npz` here for `MODE=real` |
| `run/` | Generated: `data/<site>/`, `out/<site>/final_model.json`, `state/state.json` |
| `config.sh`, `demo_lib.sh` | Shared settings and functions |
| `setup_token.sh`, `setup_tls.sh` | Create the token / TLS cert **outside** the project in `~/.fedsepsis/` (mode 600) |
| `run_demo.sh`, `stop_demo.sh` | Start and stop the demo |
| `tests/` | `test_aggregator_logic.py` (unit), `run_tests.sh` (a–i), `test_tls.sh` (Step 8), `step2_aggregator_alone.sh` |
| `collect_evidence.sh` | Writes text captures into `evidence/` |

## Run the demo (stub)

```bash
cd ~/fedsepsis-deploy
./setup_token.sh            # once: creates ~/.fedsepsis/token (never printed)
./run_demo.sh               # builds, starts, waits for done=true, prints summary + aggregator log
./stop_demo.sh              # removes the 3 containers and the network; keeps ./run
```

Options:
- `EXPECTED_ROUNDS=10 ./run_demo.sh`
- `THRESHOLD=0.7 ./run_demo.sh`
- `TLS=1 ./run_demo.sh`: see TLS below.

Each `run_demo.sh` is a fresh run: it wipes `run/state` and `run/out`. It keeps `run/data`.

**Score a patient.** On the VM:

```bash
curl -s -X POST -d '{"patient_id":"hospA-0003","hour":10}' http://127.0.0.1:8101/score
```

From the laptop, open the tunnel first:

```bash
ssh -N -L 8101:127.0.0.1:8101 -L 8102:127.0.0.1:8102 l1cc
```

Then run the same curl against `localhost:8101`. The response is `{hour, score, alert, threshold, final_model}`.

## Run the tests

```bash
./tests/step2_aggregator_alone.sh   # aggregator alone, each endpoint with curl (temporary 127.0.0.1:18000)
./tests/run_tests.sh                # a–i; runs a fresh stub demo and leaves it running
./tests/test_tls.sh                 # optional TLS checks; leaves a TLS demo running
./collect_evidence.sh               # captures into evidence/ (run while a demo is up)
```

The unit tests on their own:

```bash
docker run --rm -v $PWD:/w:ro --entrypoint /opt/venv/bin/python registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest -B /w/tests/test_aggregator_logic.py
```

## How to get the data (not in this repository)

No data, patient-level output, key or token is committed (see `.gitignore`). To rebuild the inputs:

1. Download the public **PhysioNet/CinC Challenge 2019** training data (hospital systems A and B, hourly `.psv` files):
   <https://physionet.org/content/challenge-2019/1.0.0/>. Put `training_setA/` and `training_setB/` under the project's
   `data/` folder (`config.py: DATA_DIR`).
2. In the Dr Sepsis project (Reju's folder, `src/`):
   - `src/load_data.py` parses the `.psv` files into one table per hospital.
   - `src/fed_data.py: build_fed_features` builds the 138 causal features: `_last`, `_mean6`, `_delta6` and `_since` for
     34 variables, plus Age and Gender. Each site fills missing values with its own training median.
   - `src/fed_experiment.py` runs the sweep (outputs in `outputs/fed/`).
   - `python src/fed_demo.py --dirs outputs/fed/raw outputs/fed/raw_vm` exports `outputs/demo/`: `site_big.npz`,
     `site_small.npz`, `small_test_patients.npz`, `small_val_patients.npz`, the model JSONs, `demo_patient.json`,
     `repro_config.json` and `SHA256SUMS.txt`.
3. Copy `outputs/demo/` and the `fedsepsis/` library to the VM as `~/handoff/outputs/demo/` and `~/handoff/fedsepsis/`.
   Then run `./setup_real.sh`, which checks every file against `SHA256SUMS.txt` before use.

The row conventions (training rows vs alert-metric rows, `keep_row`, onset) are in `HANDOFF.md`, which ships with the export.

## Real mode (MODE=real): Reju's handoff, library 0.2.0

Inputs are read-only from `~/handoff` (`REAL_DIR=~/handoff/outputs/demo`, `LIB_SRC=~/handoff/fedsepsis`). See `HANDOFF.md` there.

```bash
./setup_real.sh        # sha256sum -c the handoff's SHA256SUMS.txt (18 files), then copy fedsepsis into ./lib
MODE=real RUN_LABEL=tuned-40x3 ALLOW_JOBS="fedcurve,fedseed_[0-9]+" ./run_demo.sh
MODE=real EXPECTED_ROUNDS=5 RUN_LABEL=short-demo-5r ALLOW_JOBS="fedcurve,fedseed_[0-9]+" ./run_demo.sh   # "short demo run"
./tests/test_real_endpoints.sh      # /audit, /retune, /patient, /score vs HANDOFF.md section 6 + allowed fields
python3 tools/compare_models.py results/tuned-40x3/final_model.json ~/handoff/outputs/demo/model_global.json
```

- **Pre-flight.** `run_demo.sh` refuses to start if MemAvailable is under 3 GB, or if any other container or busy process is
  running. `ALLOW_JOBS` lists the containers the team agreed may run alongside (Reju's sweep); the memory floor still applies.
- **Training settings** come from `repro_config.json` (setup 4): 40 rounds x 3 local epochs, lr 0.1, l2 0.001,
  class_weight 30, zero weights, bias -1.0. Normalisation is pooled over both sites. Memory: hospA 2 GB, hospB 1 GB,
  aggregator 512 MB, 1 CPU each.
- **Data per container** (read-only mounts; each container sees only its own files):

  | | training | /audit, /score, /patient | /retune |
  |---|---|---|---|
  | hospA | `site_big.npz` | `site_big.npz` | `site_big.npz` |
  | hospB | `site_small.npz` | `small_test_patients.npz` (all rows) | `small_val_patients.npz`, as in the experiment (override: `RETUNE_DATA_hospB`) |

  Alert metrics use **all rows** of the test file, as HANDOFF.md sections 3 and 5 specify. `keep_row` is for AUROC only and is
  not used. `onset_by_row` is only returned by `GET /patient`, for the onset marker. Rows must be NaN-free: the client checks
  every file at load and fails with a clear `training_failed ... DataError` if not.
- **Hospital endpoints** (port 8100, published on 127.0.0.1:8101 / 8102). Rates and thresholds come only from
  `fedsepsis.alert_rate` and `fedsepsis.threshold_for_budget`:
  - `POST /audit {"model": "borrowed"|"federated"|"retuned", "threshold"?}` -> `{alerts_per_100_patient_days, patients, patient_days, threshold}`.
    Default model is `borrowed` (`model_big_asis.json`). Stored thresholds come from `demo_patient.json`: borrowed 0.516850 (setup 1 @25),
    federated 0.428312 (setup 4 @25), retuned = the last `/retune` result.
  - `POST /retune {"model": "borrowed"|"federated", "budget": 25}` -> `{threshold, alerts_per_100_patient_days}` (rate on the re-tune data).
    The result is stored, also in `/out/retuned.json`, so it survives a restart.
  - `POST /score {"patient_id", "hour", "model"?}` and `GET /patient?patient_id=&model=` (full hourly replay with alert hours,
    onset and window). Default model is `federated`. `federated` is this run's final global model; it is available after training.
- **Results** (`results/<label>/`): final model with `feature_names`, aggregator and hospital logs, runtime.

Measured on the VM, 2026-10-17, while Reju's sweep was running:
- tuned 40 x 3 run: 51.9 s. Max |w - model_global.w| = 5.5e-8, |b diff| = 1.8e-9, mean/std identical.
- The test-set audit reproduces HANDOFF section 6 exactly: borrowed 11.666600, setup-2 threshold 23.995858, federated 22.453905.
- Replay of patients 71 and 13561 matches `demo_patient.json` (alerts identical, scores within 2.4e-8).
- Re-tune on `small_val_patients.npz` reproduces the experiment. Borrowed: 0.425315265515, giving 23.995858 on test
  (setup 2). Federated: 0.42831155832, giving 22.453905 (setup 4). An earlier run re-tuned on `site_small.npz`
  (borrowed 0.415496, test 25.61), which is why the data source is a setting.
- API for the demo page: `DEMO_API.md`.

## Stretch: 3-site secure aggregation (`fedsecure/`, `run_secure.sh`)

This is its own package, standard library only, and is not part of `fedsepsis/`. It runs beside the main demo with its
own names (`sec-aggregator`, `sec-hospA1`, `sec-hospA2`, `sec-hospB`), network (`fedsecure-net`), state (`run/secure/`)
and ports (127.0.0.1:8111 to 8113).

**Sites.** hospA1 and hospA2 are hospital A split in two by patient. **This is a simulation**: for now the split is a
random 50/50 by patient (seed 2026). Run with `SPLIT_MAP=map.json` to split by Reju's patient-to-ICU map (medical /
surgical) instead. hospB is the small hospital.

**How it works.**
- Every pair of sites shares a 32-byte seed. The seeds live in `~/.fedsepsis/pairs/`, outside the project. Each
  hospital container mounts only its own pairs' seeds; the aggregator gets none.
- For each message, both sites in a pair derive the same mask, `SHAKE-256(seed | "round-<r>")` (or `"stats"`). The site
  with the smaller id adds it and the other subtracts it.
- Values are fixed-point integers mod 2^128 (resolution 2^-40), so the masks cancel **exactly** in the all-site sum,
  and one masked value is uniform on [0, 2^128).
- Each site sends masked `n*w` and `n*b`, with `n` in the clear. The normalisation `sum`/`sumsq` are masked the same way.
- The aggregator (`SECURE=1`) sums mod 2^128, decodes and divides by the total `n`. Its state file holds only masked
  values. The JSON keys are unchanged (`site_id, n, sum, sumsq, round, w, b`), so the allowed-fields check still applies.

```bash
SECURE=0 RUN_LABEL=secure3-plain  ./run_secure.sh      # baseline: same 3 sites, plain FedAvg
SECURE=1 RUN_LABEL=secure3-masked ./run_secure.sh      # masked
./tests/test_secure_e2e.sh                             # both runs + unit tests + comparison -> evidence/test_secure_aggregation.txt
docker run --rm -v $PWD:/w:ro --entrypoint /opt/venv/bin/python <image> -B /w/tests/test_fedsecure.py    # unit tests only
```

**Measured on the VM** (2026-10-17, 40 rounds x 3 local epochs, random split; `evidence/test_secure_aggregation.txt`):
- Masked vs plain FedAvg on the same 3 sites: max |w diff| 1.1e-16, b identical. That passes the 1e-9 bound.
- Runtime: masked 38.6 s, plain 38.1 s.
- The aggregator's stored hospB normalisation sums look uniform on [0, 2^128) (KS 0.056 vs a 1% critical value of 0.139).
- Allowed fields: 3 stats posts + 120 updates, keys within `{site_id, n, sum, sumsq, round, w, b}`.
- Unit tests: 9/9. One masked update has KS 0.050 and correlation +0.06 with the true n*w; decoded alone, it is off by
  about 7.7e21 x |n*w|.

**What it protects.** An honest-but-curious aggregator learns only the sum over all three sites, never one site's
weights or normalisation sums.

**What it does not protect.**
- The aggregator still sees each site's `n` and the final model.
- No dropout recovery: if one site fails, its partners' masks don't cancel and the round can't be decoded (by design;
  real protocols add secret-shared seed recovery).
- The aggregator colluding with one site can learn the sum of the other two.
- Seeds are pre-shared files rather than a Diffie-Hellman key agreement.
- With only two sites it protects nothing, because each site can subtract its own update from the average. That's why
  this uses three.
- Nothing here hides data from the VM's administrators (no Secure Execution).

## What is NOT implemented / not protected

- **No Secure Execution** (IBM Secure Execution for Linux) on this VM. It is an ordinary z/VM guest, so the host,
  hypervisor and VM admins can see container memory, data and keys.
- **On-chip crypto (CPACF) is not claimed.** The processor has it, and a benchmark shows OpenSSL on this VM uses it, but we
  could not confirm that our training traffic did (the counters need root). See `HARDENING.md`, B3.
- **The demo port has no authentication.** The hospital `/score`, `/audit`, `/retune` and `/patient` endpoints are plain HTTP
  bound to `127.0.0.1`, so they're reachable only on the VM or through the ssh tunnel.
- **One shared API token**, in addition to mutual TLS. The client certificate identifies the site (its CN must equal the
  posted `site_id`); the token only gates the API.
- **Secure aggregation** (stretch, `fedsecure/`) has no dropout recovery and uses pre-shared pair seeds rather than
  Diffie-Hellman. Two-site runs (the main demo) do not use it, because with two sites it cannot hide anything.
- **No differential privacy.** In the two-site demo the aggregator sees each site's sums and weights.
- **The throwaway CA has no revocation and no hardware key storage.** Keys are files on the VM (mode 600).
- **No automatic restart policy.** A crashed container stays down until `./run_live.sh` is rerun.
- The stub (`MODE=stub`) trains nothing. Its weights are the global model plus seeded noise.

## TLS and mutual TLS

The live stacks run **mutual TLS** (`MTLS=1`, see `HARDENING.md`):
- The aggregator accepts TLS 1.3 only, and requires a client certificate from the project CA in `~/.fedsepsis/mtls/`.
- Each hospital presents its own certificate (CN = site id), and pins the aggregator's certificate.
- The aggregator rejects a POST whose `site_id` differs from the certificate's CN (403).

`./setup_mtls.sh` creates the CA and certificates on first use. `run_live.sh` calls it if they're missing.

The older one-way TLS option (`TLS=1` with `./setup_tls.sh`, tested by `tests/test_tls.sh`) still works, but it's superseded.

## Where secrets live (never commit or share them)

All of these are created **on the machine that runs the stack** by the setup scripts, live outside the project, and are mode 600.
A new person or machine generates its own; nothing needs to be copied from this VM.

| Path | What | Created by |
|---|---|---|
| `~/.fedsepsis/token`, `~/.fedsepsis/curl-header` | shared API token (and a curl header file, so it never appears on a command line) | `./setup_token.sh` |
| `~/.fedsepsis/mtls/` | CA key + certificate, aggregator and per-site client certificates (`hospA`, `hospB`, `hospA1`, `hospA2`, `monitor`) | `./setup_mtls.sh` |
| `~/.fedsepsis/pairs/*.seed` | pairwise seeds for secure aggregation (rotated every secure run) | `./setup_pairs.sh` (called by `run_secure.sh`) |
| `~/.fedsepsis/tls/` | one-way TLS certificate (superseded option) | `./setup_tls.sh` |
| `~/.fedsepsis-hardening/`, `~/.fedsepsis-mtest/` | the test stacks' own token and CA | `run_hardened.sh` / `tests/verify_merge.sh` |

- **Containers get the token through `-e TOKEN`**, with the value taken from the environment, so it never appears on a
  command line or in the project.
- **`docker inspect <container>` shows `TOKEN` in its Env**, so never paste that output anywhere. `collect_evidence.sh`
  captures only selected inspect fields and finishes with a token-leak scan.
- **The SSH key for the VM is personal**: access comes from the organisers. It is not part of this project.

## Optional extras: CPACF benchmark and NNPA scoring

- `tests/cpacf_bench.sh`: CPACF on vs masked on our own mutual-TLS settings -> `evidence-hardening/cpacf_tls_bench.txt`; summary in `HARDENING.md` B3.
- `NNPA.md`: optional scoring on the IBM Z accelerator (`NNPA_SCORE=1`, off by default; `hospital/nnpa_scoring.py`,
  `hospital/Dockerfile.nnpa`, image tag `fedsepsis-hospital:nnpa`, `tests/test_nnpa_parity.py`). Measured: it works and stays within the
  agreed 0.1 alerts per 100 patient-days of the CPU path, but it is not faster for this model.
