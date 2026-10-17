"""FedSepsis hospital client.

Main thread: federated training loop against the aggregator.
Second thread: local HTTP server:
  GET  /health
  POST /score   {patient_id, hour, model?}          -> {hour, score, alert, threshold, model, final_model}
  GET  /patient ?patient_id=..&model=..             -> full hourly replay of one patient (+ onset marker)
  POST /audit   {model?, threshold?}                -> {alerts_per_100_patient_days, patients, patient_days, threshold}
  POST /retune  {model?, budget}                    -> {threshold, alerts_per_100_patient_days}
Only aggregates (stats, weights) leave this process; patient rows never do.
"""
import hashlib
import hmac
import http.client
import importlib
import json
import math
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SITE_ID = os.environ["SITE_ID"]
AGG = os.environ.get("AGGREGATOR_URL", "http://aggregator:8000").rstrip("/")
EXPECTED_ROUNDS = int(os.environ.get("EXPECTED_ROUNDS", "5"))
MODE = os.environ.get("MODE", "stub")
DATA_PATH = os.environ.get("DATA_PATH", "/data/site.json")               # training rows
EVAL_DATA_PATH = os.environ.get("EVAL_DATA_PATH") or DATA_PATH            # /audit, /score, /patient
RETUNE_DATA_PATH = os.environ.get("RETUNE_DATA_PATH") or DATA_PATH        # /retune (scores only, no labels)
BORROWED_MODEL_PATH = os.environ.get("BORROWED_MODEL_PATH", "")          # model_big_asis.json
DEMO_PATIENT_PATH = os.environ.get("DEMO_PATIENT_PATH", "")              # demo_patient.json: stored thresholds
THRESHOLD = float(os.environ.get("THRESHOLD", "0.5"))                     # stub mode only
OUT_DIR = os.environ.get("OUT_DIR", "/out")
PORT = int(os.environ.get("PORT", "8100"))
POLL_S = float(os.environ.get("POLL_S", "0.5"))
WAIT_TIMEOUT_S = float(os.environ.get("WAIT_TIMEOUT_S", "900"))
# local_train cfg. In MODE=real run_demo.sh fills these from repro_config.json (setup4 + class_weight + l2).
# The real library reads lr/epochs/l2/class_weight/anchor; site_id/seed are only used by the stub's noise.
TRAIN_CFG = {"lr": float(os.environ.get("LR", "0.1")), "epochs": int(os.environ.get("EPOCHS", "1")),
             "l2": float(os.environ.get("L2", "0.0")), "class_weight": float(os.environ.get("CLASS_WEIGHT", "1.0")),
             "anchor": None, "site_id": SITE_ID, "seed": int(os.environ.get("SEED", "42"))}

TOKEN = os.environ.get("TOKEN", "")
if not TOKEN and os.environ.get("TOKEN_FILE"):
    with open(os.environ["TOKEN_FILE"]) as _f:
        TOKEN = _f.read().strip()
if not TOKEN:
    sys.exit("TOKEN (or TOKEN_FILE) must be set")

# stub/ is baked into the image; the real package is mounted at /app/lib.
LIB_DIR = os.environ.get("FEDSEPSIS_PATH", "/app/stub" if MODE == "stub" else "/app/lib")
sys.path.insert(0, LIB_DIR)
fedsepsis = importlib.import_module("fedsepsis")
HAS_RATE_FNS = hasattr(fedsepsis, "alert_rate") and hasattr(fedsepsis, "threshold_for_budget")


def log(event, rnd="-", **extra):
    parts = [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             f"event={event}", f"site={SITE_ID}", f"round={rnd}"]
    parts += [f"{k}={v}" for k, v in extra.items()]
    print(" ".join(parts), flush=True)


# ---- JSON boundary: plain floats / ints / lists only (no numpy, no binary) --
def plain(v):
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if hasattr(v, "tolist"):
        v = v.tolist()
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float) or hasattr(v, "__float__"):
        f = float(v)
        if not math.isfinite(f):
            raise ValueError("non-finite value at JSON boundary")
        return f
    return v


# ---- data -------------------------------------------------------------------
class DataError(Exception):
    """Fatal: a data file is unusable (missing keys, NaN, shape mismatch)."""


TRAIN_KEYS = ("X", "y", "patient_id", "hour", "feature_names")
EVAL_KEYS = ("X", "patient_id", "hour", "feature_names")


def load_npz(path, keys, optional=()):
    import numpy as np
    with np.load(path, allow_pickle=False) as z:
        missing = set(keys) - set(z.files)
        if missing:
            raise DataError(f"{path}: missing keys {sorted(missing)}")
        d = {k: z[k] for k in list(keys) + [k for k in optional if k in z.files]}
    X = d["X"]
    n = X.shape[0]
    if X.ndim != 2 or X.shape[1] != len(d["feature_names"]) or any(len(d[k]) != n for k in d if k not in ("X", "feature_names")):
        raise DataError(f"{path}: shape mismatch, X{X.shape}, feature_names{d['feature_names'].shape}")
    # The library requires NaN-free engineered rows (missing values are filled locally, before calling).
    bad_rows = 0
    for a in range(0, n, 65536):
        bad_rows += int(np.isnan(X[a:a + 65536]).any(axis=1).sum())
    if bad_rows:
        raise DataError(f"{path}: {bad_rows} of {n} rows contain NaN; fill missing values locally "
                        "before training or scoring (fedsepsis needs NaN-free rows)")
    d["feature_names"] = [str(s) for s in d["feature_names"].tolist()]
    return d


def load_training(path):
    if MODE == "real":
        return load_npz(path, TRAIN_KEYS)
    with open(path) as f:
        return json.load(f)


class EvalSet:
    """Rows grouped per patient as contiguous slices (zero-copy views), hour order.
    patients[i] is what the library expects for one patient: row t = hour t."""

    def __init__(self, name, d):
        self.name = name
        X, pid, hour = d["X"], d["patient_id"], d["hour"]
        n = len(pid)
        if hasattr(pid, "dtype"):
            import numpy as np
            order = np.lexsort((hour, pid))
            if not np.array_equal(order, np.arange(n)):
                log("eval_rows_reordered", data=name)          # one copy, only if the file was not sorted
                X, pid, hour = X[order], pid[order], hour[order]
            cuts = np.flatnonzero(pid[1:] != pid[:-1]) + 1
            starts, ends = np.r_[0, cuts].tolist(), np.r_[cuts, n].tolist()
            ids = pid[starts].tolist()
            consecutive = bool((hour[starts] == 0).all()) and bool((np.diff(hour)[np.setdiff1d(np.arange(n - 1), cuts - 1)] == 1).all())
            onset_col = d.get("onset_by_row")
            self.onset = [None if onset_col is None or not math.isfinite(float(onset_col[s])) else float(onset_col[s]) for s in starts]
        else:
            order = sorted(range(n), key=lambda i: (pid[i], hour[i]))
            X, pid, hour = [X[i] for i in order], [pid[i] for i in order], [hour[i] for i in order]
            starts = [i for i in range(n) if i == 0 or pid[i] != pid[i - 1]]
            ends = starts[1:] + [n]
            ids = [pid[s] for s in starts]
            consecutive = all(hour[s] == 0 for s in starts)
            self.onset = [None] * len(starts)
        if not consecutive:
            log("warning_hours_not_consecutive", data=name)   # the library treats row t as hour t
        self.X, self.hour = X, hour
        self.bounds = list(zip(starts, ends))
        self.index = {str(p): k for k, p in enumerate(ids)}
        self.patients = [X[s:e] for s, e in self.bounds]
        self.rows = n

    def rows_of(self, k):
        s, e = self.bounds[k]
        hours = self.hour[s:e]
        return self.X[s:e], [int(h) for h in (hours.tolist() if hasattr(hours, "tolist") else hours)]


def load_model_file(path):
    with open(path) as f:
        m = json.load(f)
    return m.get("model", m)   # accept a bare model or our final_model.json wrapper


# ---- aggregator HTTP(S) -----------------------------------------------------
class PinMismatch(Exception):
    """Fatal: the aggregator presented a certificate other than the pinned one."""


def make_opener():
    if not AGG.startswith("https://"):
        return urllib.request.build_opener()
    ca = os.environ.get("TLS_CA")
    pin = os.environ.get("TLS_PIN_SHA256", "").replace(":", "").lower()
    if not ca or not pin:
        sys.exit("https AGGREGATOR_URL needs TLS_CA (pinned cert file) and TLS_PIN_SHA256")
    # Trust ONLY the pinned self-signed cert (no system CAs), verify hostname, TLS 1.3 only.
    ctx = ssl.create_default_context(cafile=ca)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3

    class PinnedConnection(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            got = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(got, pin):
                self.sock.close()
                raise PinMismatch(f"aggregator cert sha256 {got[:16]}... does not match pin {pin[:16]}...")

    class PinnedHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(PinnedConnection, req, context=ctx)

    return urllib.request.build_opener(PinnedHandler())


OPENER = None


def call(method, path, body=None, ok_statuses=(200,)):
    global OPENER
    OPENER = OPENER or make_opener()
    data = json.dumps(plain(body)).encode() if body is not None else None
    req = urllib.request.Request(AGG + path, data=data, method=method,
                                 headers={"X-Token": TOKEN, "Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        payload = json.loads(e.read() or b"{}")
        if e.code in ok_statuses:
            return e.code, payload
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {payload.get('error')}")


def poll(what, fn):
    """Call fn until it returns a truthy value. Tolerates the aggregator being down (restarts)."""
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    last_err = None
    while time.monotonic() < deadline:
        try:
            v = fn()
            if v:
                return v
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if isinstance(getattr(e, "reason", e), ssl.SSLCertVerificationError):
                raise  # wrong or untrusted certificate: fail fast, do not retry
            if str(e) != last_err:
                log("aggregator_unreachable", waiting_for=what, error=type(e).__name__)
                last_err = str(e)
        time.sleep(POLL_S)
    raise TimeoutError(f"timed out waiting for {what}")


def post_retry(path, body):
    return poll(f"POST {path}", lambda: call("POST", path, body)[1])


# ---- shared state for the HTTP endpoints --------------------------------------
class Shared:
    lock = threading.Lock()
    model = None       # current or final global model {w, b, mean, std, feature_names}
    final = False
    feature_names = None
    eval = None        # EvalSet for /audit, /score, /patient
    retune = None      # EvalSet for /retune
    borrowed = None    # model_big_asis.json
    thresholds = {}    # stored per model: borrowed / federated from demo_patient.json, retuned after POST /retune
    retuned_base = None
    status = "starting"


def with_names(model):
    """The aggregator carries only numbers; attach this site's feature list (same order as its X columns)."""
    if model is not None and Shared.feature_names is not None:
        model = dict(model, feature_names=Shared.feature_names)
    return model


def set_model(model, final=False):
    with Shared.lock:
        Shared.model = model
        Shared.final = final


def resolve_model(name):
    """-> (model dict, stored threshold or None, error message or None)"""
    with Shared.lock:
        if name == "borrowed":
            return Shared.borrowed, Shared.thresholds.get("borrowed"), None if Shared.borrowed else "no borrowed model mounted"
        if name == "federated":
            if not Shared.final:
                return None, None, f"federated training not finished (status: {Shared.status})"
            return Shared.model, Shared.thresholds.get("federated"), None
        if name == "retuned":
            if Shared.retuned_base is None:
                return None, None, "no retuned threshold yet: POST /retune first"
            base = Shared.retuned_base
        else:
            return None, None, "model must be borrowed, federated or retuned"
    m, _, err = resolve_model(base)
    return m, Shared.thresholds.get("retuned"), err


def load_side_inputs(train):
    """Everything the endpoints need besides the training rows. Runs before training starts."""
    if MODE != "real":
        Shared.eval = EvalSet("training", train)
        Shared.thresholds = {"federated": THRESHOLD}
        return
    Shared.eval = EvalSet(os.path.basename(EVAL_DATA_PATH), train if EVAL_DATA_PATH == DATA_PATH
                          else load_npz(EVAL_DATA_PATH, EVAL_KEYS, optional=("onset_by_row",)))
    Shared.retune = Shared.eval if RETUNE_DATA_PATH == EVAL_DATA_PATH else EvalSet(
        os.path.basename(RETUNE_DATA_PATH), train if RETUNE_DATA_PATH == DATA_PATH else load_npz(RETUNE_DATA_PATH, EVAL_KEYS))
    log("eval_data_loaded", eval=Shared.eval.name, eval_patients=len(Shared.eval.patients), eval_rows=Shared.eval.rows,
        retune=Shared.retune.name, retune_patients=len(Shared.retune.patients), retune_rows=Shared.retune.rows)
    if BORROWED_MODEL_PATH:
        Shared.borrowed = load_model_file(BORROWED_MODEL_PATH)
        same = Shared.borrowed.get("feature_names") == Shared.feature_names
        log("borrowed_model_loaded", features=len(Shared.borrowed.get("w", [])), feature_names_match=same)
        if not same:
            raise DataError("borrowed model feature_names differ from this site's data columns")
    if DEMO_PATIENT_PATH:
        with open(DEMO_PATIENT_PATH) as f:
            t = json.load(f)["thresholds"]
        Shared.thresholds = {"borrowed": float(t["asis_big_threshold_from_big_validation"]),
                             "federated": float(t["federated_best_threshold_from_small_validation"])}
        log("stored_thresholds", borrowed=Shared.thresholds["borrowed"], federated=Shared.thresholds["federated"],
            budget=t.get("budget_alerts_per_100_patient_days"))
    saved = os.path.join(OUT_DIR, "retuned.json")
    if os.path.exists(saved):                                   # survive a container restart
        with open(saved) as f:
            r = json.load(f)
        Shared.retuned_base, Shared.thresholds["retuned"] = r["model"], float(r["threshold"])
        log("retuned_threshold_restored", base=r["model"], threshold=r["threshold"])


def training_loop():
    Shared.status = "waiting_for_aggregator"
    poll("aggregator /health", lambda: call("GET", "/health")[1].get("ok"))
    log("aggregator_healthy")

    t0 = time.monotonic()
    data = load_training(DATA_PATH)
    Shared.feature_names = data.get("feature_names")
    log("data_loaded", rows=len(data["y"]), features=len(Shared.feature_names or data["X"][0]),
        mode=MODE, stub=getattr(fedsepsis, "IS_STUB", False), nan_free=True, seconds=f"{time.monotonic() - t0:.1f}")
    load_side_inputs(data)
    log("train_cfg", **{k: TRAIN_CFG[k] for k in ("lr", "epochs", "l2", "class_weight")})

    stats = plain(fedsepsis.local_stats(data["X"]))
    post_retry("/stats", {"site_id": SITE_ID, "n": stats["n"], "sum": stats["sum"], "sumsq": stats["sumsq"]})
    log("stats_posted")

    Shared.status = "waiting_for_norm"
    poll("norm ready", lambda: call("GET", "/norm")[1].get("ready"))
    log("norm_ready")

    Shared.status = "training"
    for r in range(EXPECTED_ROUNDS):
        g = poll(f"global round {r}", lambda: (lambda g: g if g["round"] >= r else None)(call("GET", "/global")[1]))
        if g["done"] or g["round"] > r:
            log("round_already_closed", r, aggregator_round=g["round"])
            continue
        set_model(with_names(g["model"]))
        t0 = time.monotonic()
        upd = plain(fedsepsis.local_train(g["model"], data["X"], data["y"], dict(TRAIN_CFG, round=r)))
        train_s = f"{time.monotonic() - t0:.2f}"
        status, resp = poll(f"POST /update round {r}",
                            lambda: call("POST", "/update", {"site_id": SITE_ID, "round": r, "w": upd["w"],
                                                             "b": upd["b"], "n": upd["n"]}, ok_statuses=(409,)))
        if status == 409:
            log("update_rejected", r, reason=resp.get("error"))
        else:
            log("update_posted", r, duplicate=resp.get("duplicate", False), train_seconds=train_s)
        poll(f"round {r} to close", lambda: (lambda g: g["round"] > r or g["done"])(call("GET", "/global")[1]))

    g = poll("done", lambda: (lambda g: g if g["done"] else None)(call("GET", "/global")[1]))
    final = with_names(g["model"])
    set_model(final, final=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = os.path.join(OUT_DIR, "final_model.json.tmp")
    with open(tmp, "w") as f:
        json.dump({"site_id": SITE_ID, "round": g["round"], "train_cfg": {k: TRAIN_CFG[k] for k in ("lr", "epochs", "l2", "class_weight")},
                   "model": final}, f, indent=1)
    os.replace(tmp, os.path.join(OUT_DIR, "final_model.json"))
    Shared.status = "done"
    log("final_model_written", g["round"], path=os.path.join(OUT_DIR, "final_model.json"))


# ---- local HTTP server --------------------------------------------------------
CORS_ORIGIN = re.compile(os.environ.get("CORS_ORIGIN_REGEX", r"http://(localhost|127\.0\.0\.1)(:\d{1,5})?"))


def _num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi


class Handler(BaseHTTPRequestHandler):
    server_version = "fedsepsis-hospital"

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        # The demo page is served from the laptop (http://localhost[:port]) and reaches us through the ssh tunnel.
        origin = self.headers.get("Origin", "")
        if CORS_ORIGIN.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):  # CORS preflight for POST with Content-Type: application/json
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json_body(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/health":
            return self._send(200, {"ok": True, "site_id": SITE_ID, "status": Shared.status,
                                    "final_model": Shared.final, "mode": MODE,
                                    "eval_data": Shared.eval.name if Shared.eval else None,
                                    "retune_data": Shared.retune.name if Shared.retune else None})
        if url.path == "/patient":
            q = dict(urllib.parse.parse_qsl(url.query))
            return self._patient(q)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        routes = {"/score": self._score, "/audit": self._audit, "/retune": self._retune}
        fn = routes.get(self.path)
        if fn is None:
            return self._send(404, {"error": "not found"})
        body = self._json_body()
        if body is None:
            return self._send(400, {"error": "body must be a JSON object"})
        fn(body)

    def _patient_rows(self, pid):
        ev = Shared.eval
        if ev is None:
            return None, None, None, (503, {"error": "data not loaded yet", "status": Shared.status})
        k = ev.index.get(str(pid))
        if k is None:
            return None, None, None, (404, {"error": f"patient not in {ev.name}"})
        X, hours = ev.rows_of(k)
        return k, X, hours, None

    def _model_for(self, name):
        model, thr, err = resolve_model(name)
        if err:
            return None, None, (409 if "retune" in err or "finished" in err else 400, {"error": err})
        if thr is None:
            return None, None, (409, {"error": f"no stored threshold for model {name}"})
        return model, thr, None

    def _score(self, body):
        try:
            pid, hour = str(body["patient_id"]), int(body["hour"])
        except (KeyError, TypeError, ValueError):
            return self._send(400, {"error": "body must be {patient_id, hour, model?}"})
        name = body.get("model", "federated")
        model, thr, err = self._model_for(name)
        if err:
            return self._send(*err)
        k, X, hours, err = self._patient_rows(pid)
        if err:
            return self._send(*err)
        n = sum(1 for h in hours if h <= hour)      # hours are sorted, so this is a prefix
        if n == 0:
            return self._send(404, {"error": "no rows for that patient up to that hour"})
        scored = plain(fedsepsis.score_patient(model, X[:n], thr))
        last = dict(scored[-1], hour=hours[n - 1])   # library numbers rows 0..n-1; report the real hour
        log("score", model=name, patient_rows=n)     # no patient id or score in the log
        self._send(200, {"hour": last["hour"], "score": last["score"], "alert": last["alert"],
                         "threshold": thr, "model": name, "final_model": Shared.final})

    def _patient(self, q):
        if "patient_id" not in q:
            return self._send(400, {"error": "query must have patient_id (and optional model)"})
        name = q.get("model", "federated")
        model, thr, err = self._model_for(name)
        if err:
            return self._send(*err)
        k, X, hours, err = self._patient_rows(q["patient_id"])
        if err:
            return self._send(*err)
        scored = plain(fedsepsis.score_patient(model, X, thr))
        onset = Shared.eval.onset[k]                 # display only: never used in /audit or /retune
        log("patient_replay", model=name, patient_rows=len(hours))
        self._send(200, {"patient_id": q["patient_id"], "model": name, "threshold": thr, "data": Shared.eval.name,
                         "hours": hours, "score": [r["score"] for r in scored], "alert": [r["alert"] for r in scored],
                         "alert_hours": [h for h, r in zip(hours, scored) if r["alert"]],
                         "onset_hour": onset, "window": None if onset is None else [onset - 12, onset + 3]})

    def _audit(self, body):
        name = body.get("model", "borrowed")
        if name not in ("borrowed", "federated", "retuned"):
            return self._send(400, {"error": "model must be borrowed, federated or retuned"})
        thr = body.get("threshold")
        if thr is not None and not _num(thr, 0.0, 1.0):
            return self._send(400, {"error": "threshold must be a number in [0, 1]"})
        if not HAS_RATE_FNS:
            return self._send(501, {"error": "this fedsepsis has no alert_rate (stub mode)"})
        model, stored, err = resolve_model(name)
        if err:
            return self._send(409 if "retune" in err or "finished" in err else 400, {"error": err})
        if thr is None and stored is None:
            return self._send(409, {"error": f"no stored threshold for model {name}: pass threshold"})
        thr = stored if thr is None else float(thr)
        t0 = time.monotonic()
        r = plain(fedsepsis.alert_rate(model, Shared.eval.patients, thr))
        log("audit", model=name, threshold=thr, patients=len(Shared.eval.patients), seconds=f"{time.monotonic() - t0:.1f}")
        self._send(200, {"alerts_per_100_patient_days": r["alerts_per_100pd"], "patients": len(Shared.eval.patients),
                         "patient_days": r["patient_days"], "threshold": thr,
                         "alerts": r["alerts"], "model": name, "data": Shared.eval.name})

    def _retune(self, body):
        name = body.get("model", "borrowed")
        if name not in ("borrowed", "federated"):
            return self._send(400, {"error": "model must be borrowed or federated"})
        budget = body.get("budget", 25)
        if not _num(budget, 1e-6, 1000):
            return self._send(400, {"error": "budget must be a positive number (alerts per 100 patient-days)"})
        if not HAS_RATE_FNS:
            return self._send(501, {"error": "this fedsepsis has no threshold_for_budget (stub mode)"})
        model, _, err = resolve_model(name)
        if err:
            return self._send(409 if "finished" in err else 400, {"error": err})
        t0 = time.monotonic()
        r = plain(fedsepsis.threshold_for_budget(model, Shared.retune.patients, float(budget)))
        with Shared.lock:
            Shared.retuned_base, Shared.thresholds["retuned"] = name, float(r["threshold"])
        rec = {"model": name, "budget": budget, "threshold": r["threshold"], "data": Shared.retune.name,
               "realized_on_retune_data": r["realized_per_100pd"]}
        tmp = os.path.join(OUT_DIR, "retuned.json.tmp")
        with open(tmp, "w") as f:
            json.dump(rec, f, indent=1)
        os.replace(tmp, os.path.join(OUT_DIR, "retuned.json"))
        log("retune", model=name, budget=budget, threshold=r["threshold"], data=Shared.retune.name,
            seconds=f"{time.monotonic() - t0:.1f}")
        self._send(200, {"threshold": r["threshold"], "alerts_per_100_patient_days": r["realized_per_100pd"],
                         "model": name, "budget": budget, "data": Shared.retune.name,
                         "patients": len(Shared.retune.patients)})


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log("start", mode=MODE, lib=LIB_DIR, lib_version=getattr(fedsepsis, "__version__", "?"),
        has_rate_fns=HAS_RATE_FNS, expected_rounds=EXPECTED_ROUNDS, port=PORT)
    try:
        training_loop()
    except Exception as e:
        Shared.status = f"failed: {type(e).__name__}"
        log("training_failed", error=f"{type(e).__name__}: {e}")
    # keep serving after training (or after a failure, for diagnosis)
    threading.Event().wait()


if __name__ == "__main__":
    main()
