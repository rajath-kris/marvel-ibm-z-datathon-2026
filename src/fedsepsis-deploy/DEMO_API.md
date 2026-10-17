# SepsisBridge demo API (hospital containers on LinuxONE)

For the demo page. All responses are JSON. Examples below are real responses from the VM (2026-10-17, MODE=real,
40-round federated model, library fedsepsis 0.2.0).

## Connect

The hospital ports are bound to 127.0.0.1 on the VM only. Open a tunnel from the laptop and leave it running:

```bash
ssh -N -L 8102:127.0.0.1:8102 l1cc
```

Add `-L 8101:127.0.0.1:8101` as well if you also want hospital A.

| Base URL (laptop) | Site | Data used by /audit, /score, /patient | Data used by /retune |
|---|---|---|---|
| `http://localhost:8102` | **hospB: the small hospital (use this one for the demo)** | `small_test_patients.npz`: 9,885 held-out patients | `small_val_patients.npz`: 591 patients |
| `http://localhost:8101` | hospA: the big hospital | its own training rows (`site_big.npz`) | the same |

**CORS.** Requests from `http://localhost:<any port>` and `http://127.0.0.1:<any port>` are allowed. The server answers the
browser preflight (`OPTIONS`, 204) for `POST` with `Content-Type: application/json`. Other origins get no
`Access-Control-Allow-Origin` header, so the browser blocks them.

**Models.** Every endpoint that scores takes a `model` name:

| `model` | What it is | Stored threshold (budget 25 alerts per 100 patient-days) |
|---|---|---|
| `borrowed` | big hospital's model used as is (`model_big_asis.json`) | 0.516850 (from the big hospital's validation set) |
| `federated` | global model trained by FedAvg over hospA + hospB in this deployment | 0.428312 (setup 4) |
| `retuned` | the model last re-tuned with `POST /retune` (currently **borrowed**), with its new threshold | 0.425315 (after `/retune borrowed 25`) |

## Endpoints

### `GET /health`
```json
{"ok": true, "site_id": "hospB", "status": "done", "final_model": true, "mode": "real",
 "eval_data": "small_test_patients.npz", "retune_data": "small_val_patients.npz"}
```
`status` is `training` while the federated run is in progress, and `done` once `federated` is available.

### `POST /audit`: shadow-mode alert rate on this hospital's own patients
Request: `{"model": "borrowed"|"federated"|"retuned", "threshold"?: number in [0,1]}`. Both fields are optional: the default
model is `borrowed`, and the default threshold is that model's stored threshold. No labels are used.

```bash
curl -s -X POST -H 'Content-Type: application/json' -d '{"model":"borrowed"}' http://localhost:8102/audit
```
```json
{"alerts_per_100_patient_days": 11.66660029469157, "patients": 9885, "patient_days": 15694.375,
 "threshold": 0.5168503567531072, "alerts": 1831, "model": "borrowed", "data": "small_test_patients.npz"}
```
Other real results: `{"model":"retuned"}` gives 23.995858 (threshold 0.425315), and `{"model":"federated"}` gives 22.453905
(threshold 0.428312). It takes about 1 to 3 s.

### `POST /retune`: pick this hospital's own threshold for an alert budget
Request: `{"model": "borrowed"|"federated", "budget": 25}`. Scores only, no labels. The result is **stored** and becomes
`model: "retuned"` for `/audit`, `/score` and `/patient`.

```bash
curl -s -X POST -H 'Content-Type: application/json' -d '{"model":"borrowed","budget":25}' http://localhost:8102/retune
```
```json
{"threshold": 0.42531526551518406, "alerts_per_100_patient_days": 25.003202254387087, "model": "borrowed",
 "budget": 25, "data": "small_val_patients.npz", "patients": 591}
```
`alerts_per_100_patient_days` here is the rate on the re-tune (validation) set. Call `/audit` with `{"model":"retuned"}`
for the rate on the test patients. Note that re-tuning the federated model replaces the stored `retuned` threshold;
re-tune `borrowed` again before the demo if you need it.

### `GET /patient?patient_id=<id>&model=<name>`: full hourly replay of one patient
`model` is optional (default `federated`). Any `patient_id` in `small_test_patients.npz` works. For the demo, use
**71** (the success example) and **13561** (the failure example) from `demo_patient.json`.

```bash
curl -s 'http://localhost:8102/patient?patient_id=71&model=federated'
```
```json
{"patient_id": "71", "model": "federated", "threshold": 0.428311566223493, "data": "small_test_patients.npz",
 "hours": [0, 1, 2, "...", 34], "score": [0.2994, 0.3024, 0.3438, "...", 0.4863],
 "alert": [false, false, false, "...", false], "alert_hours": [29],
 "onset_hour": 31.0, "window": [19.0, 34.0]}
```
- `onset_hour` and `window` (onset − 12 h to onset + 3 h) are for drawing the onset marker only. They are `null` for
  non-septic patients.
- Results for the demo patients:

  | Patient | `federated` alert hours | `borrowed` alert hours |
  |---|---|---|
  | 71 | `[29]`, in the window | `[]` |
  | 13561 | `[]` | `[]` |

- Alert rule: an alert fires when the score crosses the threshold from below, then that patient is silent for 6 hours.

### `POST /score`: score one patient up to an hour (the live "ticker")
Request: `{"patient_id": 71, "hour": 29, "model"?: "federated"}`. It scores hours 0 to `hour` and returns the last hour.

```bash
curl -s -X POST -H 'Content-Type: application/json' -d '{"patient_id":71,"hour":29}' http://localhost:8102/score
```
```json
{"hour": 29, "score": 0.4543896320605181, "alert": true, "threshold": 0.428311566223493, "model": "federated", "final_model": true}
```

## Errors
| HTTP | When | Body |
|---|---|---|
| 400 | bad JSON, unknown model, threshold outside [0,1], budget <= 0 | `{"error": "..."}` |
| 404 | unknown path, or `patient_id` not in the data | `{"error": "patient not in small_test_patients.npz"}` |
| 409 | `federated` before training is done, or `retuned` before any `/retune` | `{"error": "..."}` |
| 503 | data still loading (first seconds after a restart) | `{"error": "data not loaded yet", "status": "..."}` |

## Suggested demo flow (hospB)
1. Run `/audit` with `{"model":"borrowed"}`. Result: **11.67** alerts per 100 patient-days against a target of 25, so the borrowed threshold misfires.
2. Run `/retune` with `{"model":"borrowed","budget":25}`, then `/audit` with `{"model":"retuned"}`. Result: **24.0**. The new threshold is set locally with no labels.
3. Run `/audit` with `{"model":"federated"}`. Result: **22.45**.
4. Open the replay `/patient?patient_id=71` with `model=federated` and with `model=borrowed`. The federated model alerts at hour 29, before onset at 31; the borrowed model doesn't alert.
5. Open the same replay for patient 13561, a case both models miss. That's honest about the limits.

There's no authentication on these endpoints. They're reachable only through the ssh tunnel (localhost on the VM).
