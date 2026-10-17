"""FedSepsis hospital client.

Main thread: federated training loop against the aggregator.
Second thread: local HTTP server with GET /health and POST /score.
Only aggregates (stats, weights) leave this process; patient rows never do.
"""
import hashlib
import hmac
import http.client
import importlib
import json
import math
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SITE_ID = os.environ["SITE_ID"]
AGG = os.environ.get("AGGREGATOR_URL", "http://aggregator:8000").rstrip("/")
EXPECTED_ROUNDS = int(os.environ.get("EXPECTED_ROUNDS", "5"))
DATA_PATH = os.environ.get("DATA_PATH", "/data/site.json")
MODE = os.environ.get("MODE", "stub")
THRESHOLD = float(os.environ.get("THRESHOLD", "0.5"))
OUT_DIR = os.environ.get("OUT_DIR", "/out")
PORT = int(os.environ.get("PORT", "8100"))
POLL_S = float(os.environ.get("POLL_S", "0.5"))
WAIT_TIMEOUT_S = float(os.environ.get("WAIT_TIMEOUT_S", "900"))
TRAIN_CFG = {"site_id": SITE_ID, "seed": int(os.environ.get("SEED", "42")),
             "lr": float(os.environ.get("LR", "0.1")), "epochs": int(os.environ.get("EPOCHS", "1"))}

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
def load_data(path):
    if MODE == "real":
        import numpy as np
        with np.load(path, allow_pickle=False) as z:
            d = {k: z[k] for k in ("X", "y", "patient_id", "hour", "feature_names")}
        d["patient_id"] = [str(p) for p in d["patient_id"].tolist()]
        d["hour"] = [int(h) for h in d["hour"].tolist()]
        return d
    with open(path) as f:
        return json.load(f)


def build_patient_index(data):
    idx = {}
    for i, (p, h) in enumerate(zip(data["patient_id"], data["hour"])):
        idx.setdefault(str(p), []).append((int(h), i))
    for p in idx:
        idx[p].sort()
    return idx


def patient_rows(data, rows):
    """Contract assumption: score_patient gets {"X": rows, "hour": hours}, sorted by hour."""
    ids = [i for _, i in rows]
    X = data["X"][ids] if hasattr(data["X"], "shape") else [data["X"][i] for i in ids]
    return {"X": X, "hour": [h for h, _ in rows]}


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


# ---- shared model for /score -----------------------------------------------
class Shared:
    lock = threading.Lock()
    model = None       # current or final global model {w, b, mean, std}
    final = False
    data = None
    index = None
    status = "starting"


def set_model(model, final=False):
    with Shared.lock:
        Shared.model = model
        Shared.final = final


def training_loop():
    Shared.status = "waiting_for_aggregator"
    poll("aggregator /health", lambda: call("GET", "/health")[1].get("ok"))
    log("aggregator_healthy")

    data = load_data(DATA_PATH)
    Shared.data, Shared.index = data, build_patient_index(data)
    log("data_loaded", rows=len(data["y"]), patients=len(Shared.index), mode=MODE,
        stub=getattr(fedsepsis, "IS_STUB", False))

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
        set_model(g["model"])
        upd = plain(fedsepsis.local_train(g["model"], data["X"], data["y"], dict(TRAIN_CFG, round=r)))
        status, resp = poll(f"POST /update round {r}",
                            lambda: call("POST", "/update", {"site_id": SITE_ID, "round": r, "w": upd["w"],
                                                             "b": upd["b"], "n": upd["n"]}, ok_statuses=(409,)))
        if status == 409:
            log("update_rejected", r, reason=resp.get("error"))
        else:
            log("update_posted", r, duplicate=resp.get("duplicate", False))
        poll(f"round {r} to close", lambda: (lambda g: g["round"] > r or g["done"])(call("GET", "/global")[1]))

    g = poll("done", lambda: (lambda g: g if g["done"] else None)(call("GET", "/global")[1]))
    set_model(g["model"], final=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = os.path.join(OUT_DIR, "final_model.json.tmp")
    with open(tmp, "w") as f:
        json.dump({"site_id": SITE_ID, "round": g["round"], "model": g["model"]}, f, indent=1)
    os.replace(tmp, os.path.join(OUT_DIR, "final_model.json"))
    Shared.status = "done"
    log("final_model_written", g["round"], path=os.path.join(OUT_DIR, "final_model.json"))


# ---- local HTTP server --------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "fedsepsis-hospital"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "site_id": SITE_ID, "status": Shared.status,
                                    "final_model": Shared.final})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/score":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            pid, hour = str(body["patient_id"]), int(body["hour"])
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "body must be {patient_id, hour}"})
        with Shared.lock:
            model, final = Shared.model, Shared.final
        if model is None or model.get("mean") is None or Shared.index is None:
            return self._send(503, {"error": "no global model yet", "status": Shared.status})
        rows = [rh for rh in Shared.index.get(pid, []) if rh[0] <= hour]
        if not rows:
            return self._send(404, {"error": "no rows for that patient up to that hour"})
        scored = plain(fedsepsis.score_patient(model, patient_rows(Shared.data, rows), THRESHOLD))
        last = scored[-1]
        log("score", patient_rows=len(rows), final_model=final)  # no patient id or score in the log
        self._send(200, {"hour": last["hour"], "score": last["score"], "alert": last["alert"],
                         "threshold": THRESHOLD, "final_model": final})


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log("start", mode=MODE, lib=LIB_DIR, expected_rounds=EXPECTED_ROUNDS, port=PORT)
    try:
        training_loop()
    except Exception as e:
        Shared.status = f"failed: {type(e).__name__}"
        log("training_failed", error=f"{type(e).__name__}: {e}")
    # keep serving /score after training (or after a failure, for diagnosis)
    threading.Event().wait()


if __name__ == "__main__":
    main()
