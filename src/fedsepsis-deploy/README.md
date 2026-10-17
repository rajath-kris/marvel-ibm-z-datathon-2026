# fedsepsis-deploy

Deployment skeleton for the FedSepsis federated-learning demo on the LinuxONE VM `datathon-test` (s390x).
It runs one **aggregator** and two **hospital** containers (`hospA`, `hospB`) that talk JSON over HTTP on a private
docker network. The ML library is not ready yet, so the hospitals run against a **pure-Python stub** (`MODE=stub`).
`MODE=real` switches to the real `fedsepsis` package once it arrives.

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
2. In the SepsisBridge project (Reju's folder, `src/`):
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

## What is NOT implemented / not protected

- **By default there is no TLS.** The shared token and all aggregates (stats, weights) travel in **plain HTTP** inside the
  docker bridge network. Anyone with root or docker access on this VM can read them. TLS is opt-in (`TLS=1`, below).
- **There is no Secure Execution** (IBM Secure Execution for Linux) on this VM. It is an ordinary z/VM guest, so the host,
  hypervisor and VM admins can see container memory, data and keys.
- **One shared token for all sites.** It authenticates "a member", not *which* member. Any token holder can post as any
  `site_id`. There are no per-site credentials, and no mTLS.
- **No differential privacy and no secure aggregation.** The aggregator sees each site's exact `sum/sumsq` and weights.
- **`/score` on the hospitals has no authentication.** It is bound to `127.0.0.1` only, so it's reachable from the VM or through
  the ssh tunnel.
- The stub "trains" nothing. Its weights are the global model plus seeded noise, so the scores and alerts are meaningless.
- There is no automatic restart policy. A crashed container stays down until you rerun.

## TLS (optional, Step 8): what it does and does not do

```bash
./setup_tls.sh                # once: self-signed P-256 cert, CN/SAN=aggregator, 30 days, in ~/.fedsepsis/tls/
TLS=1 ./run_demo.sh
./tests/test_tls.sh
```

- **How it works.** The aggregator wraps its socket with the `ssl` module and **accepts TLS 1.3 only**. The private key is mounted
  read-only into the aggregator container only. Hospitals trust **only** the pinned certificate (no system CAs), check the
  hostname `aggregator`, and also compare the server certificate's SHA-256 fingerprint to `TLS_PIN_SHA256`. On a mismatch they
  stop immediately rather than retry.
- **What it protects.** The token and the aggregates are encrypted between the containers. A container that impersonates
  `aggregator` without the key is rejected (verified: a rogue self-signed cert for the same name fails, and so does a wrong pin).
- **What it doesn't protect.**
  - There is no client authentication beyond the shared token (no mTLS).
  - Anyone who can read `~/.fedsepsis/` or run docker on the VM can take the key and token.
  - It doesn't change anything about the host or hypervisor (no Secure Execution).
  - `/score` on the hospitals is still plain HTTP on localhost.
  - The TLS handshake runs in the server's accept loop, so a slow client can delay other connections (acceptable for a demo).
- **Rotating the cert:** run `./setup_tls.sh --rotate`, then rerun. The pin is read from the cert at start time.

## Where secrets live

- `~/.fedsepsis/token`, `~/.fedsepsis/curl-header`, `~/.fedsepsis/tls/aggregator.key`: all mode 600, outside the project.
- Containers get the token through `-e TOKEN` with the value taken from the environment. It never appears on a command
  line or in the project.
- `docker inspect <container>` **does** show `TOKEN` in its Env, so never paste that output anywhere. `collect_evidence.sh`
  only inspects the network, and finishes with a token-leak scan over `evidence/`.
