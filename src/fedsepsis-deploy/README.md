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

## Swapping in the real library (MODE=real)

1. Copy the package so that `lib/fedsepsis/__init__.py` exists. It's mounted read-only at `/app/lib` and
   `import fedsepsis` picks it up.
   ```bash
   scp -r fedsepsis l1cc:~/fedsepsis-deploy/lib/
   ```
2. Put the data in place. hospA gets `site_big.npz` and hospB gets `site_small.npz`. Each container sees **only its own
   file**, mounted read-only at `/data/<name>.npz`. To use other paths, set `REAL_DATA_hospA=/path/a.npz REAL_DATA_hospB=/path/b.npz`.
   ```bash
   mkdir -p data && cp site_big.npz site_small.npz data/
   ```
   The required format is numpy arrays only, with keys `X, y, patient_id, hour, feature_names`. The client always loads with
   `np.load(path, allow_pickle=False)`, so object arrays (e.g. Python-string `patient_id` with dtype `object`) are
   **rejected**. Use fixed-width unicode (`dtype='<U…'`) or integers.
3. Rerun:
   ```bash
   MODE=real ./run_demo.sh
   ```
   Then run `./tests/run_tests.sh` again. Tests (c), (e) and (i) currently use stub patient ids (`hospA-0003`) and the
   stub `site.json` filename, so adjust those lines for real data.
4. Check the real library against the frozen contract. The client calls exactly these, and converts every result to plain
   floats and lists before it is sent:
   - `local_stats(X) -> {"n","sum":[f],"sumsq":[f]}`
   - `local_train(model, X, y, cfg) -> {"w":[f],"b":f,"n":int}`. `model` is `{w, b, mean, std}`. `cfg` is
     `{site_id, round, seed, lr, epochs}` (`SEED`, `LR`, `EPOCHS` env vars).
   - `score_patient(model, patient_rows, threshold, silence_h=6) -> [{"hour","score","alert"}]`.
     **Assumption:** `patient_rows` is passed as `{"X": <rows sorted by hour>, "hour": [int]}`. In real mode `X` is a numpy
     slice. If the real library expects something else, change `patient_rows()` in `hospital/client.py`, which is one function.
   - `combine_stats` and `fedavg_aggregate` are **not called** by the deployment. The aggregator re-implements them in the
     standard library, so it never needs numpy. The unit tests check the stub's versions against the aggregator. Do the
     same with the real ones.

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
