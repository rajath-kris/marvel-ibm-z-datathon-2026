"""FedSepsis aggregator: standard library only.

Holds the federated state (normalisation stats, global model, round counter),
serves it over JSON/HTTP, and persists it to STATE_DIR after every change.
Never logs weights or data: only event name, site_id, round and request-body KEYS.
"""
import hmac
import json
import math
import os
import ssl
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STD_FLOOR = 1e-8
ALLOWED_BODY_KEYS = {"site_id", "n", "sum", "sumsq", "round", "w", "b"}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(event, site_id="-", rnd="-", **extra):
    parts = [utc_now(), f"event={event}", f"site={site_id}", f"round={rnd}"]
    parts += [f"{k}={v}" for k, v in extra.items()]
    print(" ".join(parts), flush=True)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _floats(v, name):
    if not isinstance(v, list) or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        raise ApiError(400, f"{name} must be a list of numbers")
    out = [float(x) for x in v]
    if not all(math.isfinite(x) for x in out):
        raise ApiError(400, f"{name} must be finite")
    return out


def _number(v, name):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise ApiError(400, f"{name} must be a finite number")
    return float(v)


def _masked(v, name, mod):
    """secure mode: a masked value is a JSON integer in [0, 2**128)."""
    if not isinstance(v, list) or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x < mod for x in v):
        raise ApiError(400, f"{name} must be a list of masked integers in [0, 2**128)")
    return v


def load_fedsecure():
    # aggregator.py runs with python -I (script dir not on sys.path); fedsecure sits next to it in the image (/app)
    # or one level up in the repo.
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.dirname(here)):
        if os.path.isdir(os.path.join(d, "fedsecure")) and d not in sys.path:
            sys.path.insert(0, d)
    import fedsecure
    return fedsecure


def _count(v, name):
    if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
        raise ApiError(400, f"{name} must be a positive integer")
    return v


class AggregatorState:
    """Pure aggregation logic. Thread-safe; persists via save_fn after each change."""

    def __init__(self, expected_sites, expected_rounds, state_path=None, init_bias=0.0, secure=False):
        if not expected_sites:
            raise ValueError("EXPECTED_SITES is empty")
        if expected_rounds < 1:
            raise ValueError("EXPECTED_ROUNDS must be >= 1")
        self.lock = threading.Lock()
        self.state_path = state_path
        self.sec = load_fedsecure() if secure else None
        self.s = {
            "expected_sites": sorted(expected_sites),
            "expected_rounds": expected_rounds,
            "dim": None,
            "stats": {},        # site_id -> {n, sum, sumsq}
            "norm": None,       # {mean, std} once every site has posted
            "round": 0,
            "model": {"w": None, "b": float(init_bias)},   # zero weights, bias b0 (repro_config.json: -1.0)
            "updates": {},      # site_id -> {w, b, n} for the current round
            "done": False,
            "body_keys_seen": [],
            "secure": bool(secure),   # True: sum/sumsq/w/b are pairwise-masked integers (fedsecure)
        }
        self.restored = self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self):
        if not self.state_path or not os.path.exists(self.state_path):
            return False
        with open(self.state_path) as f:
            saved = json.load(f)
        if saved.get("expected_sites") != self.s["expected_sites"] or \
                saved.get("expected_rounds") != self.s["expected_rounds"] or \
                bool(saved.get("secure")) != self.s["secure"]:
            raise ValueError(f"{self.state_path} was written for a different site list or round count; "
                             "remove it to start a fresh run")
        self.s = saved
        return True

    def _save(self):
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)

    # ---- helpers -----------------------------------------------------------
    def _check_site(self, site_id):
        if site_id not in self.s["expected_sites"]:
            raise ApiError(403, "unknown site_id")

    def note_keys(self, keys):
        with self.lock:
            seen = set(self.s["body_keys_seen"])
            if not set(keys) <= seen:
                self.s["body_keys_seen"] = sorted(seen | set(keys))
                self._save()

    # ---- endpoints ---------------------------------------------------------
    def post_stats(self, body):
        site_id = body.get("site_id")
        n = _count(body.get("n"), "n")
        if self.sec:
            ssum = _masked(body.get("sum"), "sum", self.sec.MOD)
            ssq = _masked(body.get("sumsq"), "sumsq", self.sec.MOD)
        else:
            ssum = _floats(body.get("sum"), "sum")
            ssq = _floats(body.get("sumsq"), "sumsq")
        if len(ssum) != len(ssq) or not ssum:
            raise ApiError(400, "sum and sumsq must be non-empty and the same length")
        with self.lock:
            self._check_site(site_id)
            if self.s["dim"] is None:
                self.s["dim"] = len(ssum)
                self.s["model"]["w"] = [0.0] * len(ssum)
            elif len(ssum) != self.s["dim"]:
                raise ApiError(400, f"dimension {len(ssum)} != {self.s['dim']}")
            if site_id in self.s["stats"]:
                log("stats_duplicate", site_id, self.s["round"])
                return {"ok": True, "duplicate": True}
            self.s["stats"][site_id] = {"n": n, "sum": ssum, "sumsq": ssq}
            log("stats", site_id, self.s["round"], sites_stats=len(self.s["stats"]))
            if set(self.s["stats"]) == set(self.s["expected_sites"]):
                # fixed site order, so float sums do not depend on arrival order
                per_site = [self.s["stats"][k] for k in self.s["expected_sites"]]
                if self.sec:
                    # masks cancel only in the all-site sum: the aggregator never sees one site's sums
                    per_site = [{"n": sum(v["n"] for v in per_site),
                                 "sum": self.sec.unmask_sum(v["sum"] for v in per_site),
                                 "sumsq": self.sec.unmask_sum(v["sumsq"] for v in per_site)}]
                self.s["norm"] = combine_totals(per_site)
                log("norm_ready", "-", self.s["round"], total_n=sum(v["n"] for v in self.s["stats"].values()))
            self._save()
            return {"ok": True}

    def get_norm(self):
        with self.lock:
            norm = self.s["norm"]
            return {"ready": norm is not None,
                    "mean": norm["mean"] if norm else None,
                    "std": norm["std"] if norm else None}

    def get_global(self):
        with self.lock:
            norm = self.s["norm"]
            return {"round": self.s["round"],
                    "model": {"w": self.s["model"]["w"], "b": self.s["model"]["b"],
                              "mean": norm["mean"] if norm else None,
                              "std": norm["std"] if norm else None},
                    "done": self.s["done"]}

    def post_update(self, body):
        site_id = body.get("site_id")
        rnd = body.get("round")
        if not isinstance(rnd, int) or isinstance(rnd, bool):
            raise ApiError(400, "round must be an integer")
        if self.sec:
            w = _masked(body.get("w"), "w", self.sec.MOD)          # masked n*w
            b = _masked([body.get("b")], "b", self.sec.MOD)[0]     # masked n*b
        else:
            w = _floats(body.get("w"), "w")
            b = _number(body.get("b"), "b")
        n = _count(body.get("n"), "n")
        with self.lock:
            self._check_site(site_id)
            if self.s["norm"] is None:
                raise ApiError(409, "normalisation not ready")
            if self.s["done"]:
                raise ApiError(409, "training is done")
            if rnd != self.s["round"]:
                log("update_wrong_round", site_id, rnd, current=self.s["round"])
                raise ApiError(409, f"round {rnd} != current round {self.s['round']}")
            if len(w) != self.s["dim"]:
                raise ApiError(400, f"dimension {len(w)} != {self.s['dim']}")
            if site_id in self.s["updates"]:
                log("update_duplicate", site_id, rnd)
                return {"ok": True, "duplicate": True}
            self.s["updates"][site_id] = {"w": w, "b": b, "n": n}
            log("update", site_id, rnd, updated=len(self.s["updates"]))
            if set(self.s["updates"]) == set(self.s["expected_sites"]):
                ups = [self.s["updates"][k] for k in self.s["expected_sites"]]
                if self.sec:
                    total_n = sum(u["n"] for u in ups)
                    nw = self.sec.unmask_sum(u["w"] for u in ups)
                    nb = self.sec.unmask_sum([u["b"]] for u in ups)[0]
                    self.s["model"] = {"w": [x / total_n for x in nw], "b": nb / total_n}
                else:
                    self.s["model"] = weighted_average(ups)
                self.s["updates"] = {}
                self.s["round"] += 1
                if self.s["round"] >= self.s["expected_rounds"]:
                    self.s["done"] = True
                log("round_complete", "-", rnd, new_round=self.s["round"], done=self.s["done"])
            self._save()
            return {"ok": True}

    def get_status(self):
        with self.lock:
            return {"round": self.s["round"],
                    "done": self.s["done"],
                    "sites_stats": sorted(self.s["stats"]),
                    "sites_updated_this_round": sorted(self.s["updates"]),
                    "expected_sites": self.s["expected_sites"],
                    "expected_rounds": self.s["expected_rounds"],
                    "body_keys_seen": self.s["body_keys_seen"]}


def combine_totals(stats):
    stats = list(stats)
    total_n = sum(s["n"] for s in stats)
    dim = len(stats[0]["sum"])
    mean, std = [], []
    for j in range(dim):
        m = sum(s["sum"][j] for s in stats) / total_n
        var = sum(s["sumsq"][j] for s in stats) / total_n - m * m
        mean.append(m)
        std.append(max(math.sqrt(max(var, 0.0)), STD_FLOOR))
    return {"mean": mean, "std": std}


def weighted_average(updates):
    updates = list(updates)
    total_n = sum(u["n"] for u in updates)
    dim = len(updates[0]["w"])
    w = [sum(u["w"][j] * u["n"] for u in updates) / total_n for j in range(dim)]
    b = sum(u["b"] * u["n"] for u in updates) / total_n
    return {"w": w, "b": b}


def make_handler(state, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fedsepsis-aggregator"

        def log_message(self, fmt, *args):  # silence default access log (it would include client IPs only, but keep one format)
            pass

        def _send(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self):
            got = self.headers.get("X-Token", "")
            if hmac.compare_digest(got.encode(), token.encode()):
                return True
            log("auth_rejected", "-", "-", path=self.path.split("?")[0])
            self._send(401, {"ok": False, "error": "unauthorised"})
            return False

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 10_000_000:
                raise ApiError(413, "body too large")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                raise ApiError(400, "body is not JSON")
            if not isinstance(body, dict):
                raise ApiError(400, "body must be a JSON object")
            keys = sorted(body)
            state.note_keys(keys)
            log("request_keys", body.get("site_id", "-") if isinstance(body.get("site_id"), str) else "-",
                body.get("round", "-") if isinstance(body.get("round"), int) else "-",
                path=self.path, keys=",".join(keys))
            return body

        def _dispatch(self, routes):
            if not self._authorised():
                return
            fn = routes.get(self.path.split("?")[0])
            if fn is None:
                return self._send(404, {"ok": False, "error": "not found"})
            try:
                self._send(200, fn())
            except ApiError as e:
                self._send(e.status, {"ok": False, "error": e.message})

        def do_GET(self):
            self._dispatch({
                "/health": lambda: {"ok": True},
                "/norm": state.get_norm,
                "/global": state.get_global,
                "/status": state.get_status,
            })

        def do_POST(self):
            self._dispatch({
                "/stats": lambda: state.post_stats(self._body()),
                "/update": lambda: state.post_update(self._body()),
            })

    return Handler


def main():
    token = os.environ.get("TOKEN", "")
    if not token and os.environ.get("TOKEN_FILE"):
        with open(os.environ["TOKEN_FILE"]) as f:
            token = f.read().strip()
    if len(token) < 16:
        sys.exit("TOKEN (or TOKEN_FILE) must be set to a secret of at least 16 characters")
    sites = [s.strip() for s in os.environ.get("EXPECTED_SITES", "").split(",") if s.strip()]
    rounds = int(os.environ.get("EXPECTED_ROUNDS", "5"))
    state_dir = os.environ.get("STATE_DIR", "/state")
    port = int(os.environ.get("PORT", "8000"))
    os.makedirs(state_dir, exist_ok=True)
    state = AggregatorState(sites, rounds, os.path.join(state_dir, "state.json"),
                            init_bias=float(os.environ.get("INIT_BIAS", "0.0")),
                            secure=os.environ.get("SECURE", "0") == "1")
    log("start", "-", state.s["round"], sites=",".join(state.s["expected_sites"]),
        expected_rounds=rounds, restored=state.restored, done=state.s["done"], secure=state.s["secure"])
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(state, token))
    server.daemon_threads = True
    cert, key = os.environ.get("TLS_CERT"), os.environ.get("TLS_KEY")
    if cert and key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        log("tls_enabled", "-", "-", min_version="TLSv1.3")
    else:
        log("tls_disabled", "-", "-", note="plain HTTP inside the docker network")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
