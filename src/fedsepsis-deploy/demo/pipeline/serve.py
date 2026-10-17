#!/usr/bin/env python3
"""Dr Sepsis pipeline demo: a tiny local server for the page (index.html).

  python3 demo/pipeline/serve.py --prep        live: start the vid-* demo stack, serve the page on 127.0.0.1:8300
  python3 demo/pipeline/serve.py               live: use a demo stack that is already running
  python3 demo/pipeline/serve.py --replay-only replay: needs no VM, no docker (works on a laptop)

Live steps call the same endpoints as demo/demo.sh (the hospital's /audit and /retune, the aggregator log). They run
against the SEPARATE demo stack (containers vid-*), never the live hospA/hospB/rev-* stacks, because /retune stores a
threshold inside the hospital and a federated round needs fresh containers. Every number the page shows comes from a
service answer or a saved results file. Nothing is printed that is secret: no token, certificate, URL or path.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import signal
import statistics as st
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent                                   # ~/fedsepsis-deploy
REPLAY_DIR = HERE / "replay"
FED_RESULTS = Path(os.environ.get("FED_RESULTS", Path.home() / "ideas/idea2-fedsepsis/outputs/fed/raw_vm"))

# The demo stack of demo/demo.sh: reverse direction (small hospital = A, big = B), published ports shifted by 1000.
SMALL_URL = "http://127.0.0.1:9202"
AGG, SMALL, BIG = "vid-fedsepsis-aggregator", "vid-hospA", "vid-hospB"
BUDGET = 25
FIELD_TYPES = {"site_id": "hospital id", "n": "counts", "sum": "sums", "sumsq": "sums", "round": "round number",
               "w": "weights", "b": "weights"}

LOCK = threading.Lock()
RUN = {}                                                    # step number -> result of this live session
STACK = {"state": "unknown", "error": None}                 # unknown | ready | preparing | down | failed
ARGS = None


class StepError(Exception):
    pass


def sh(cmd, timeout=120, env=None):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, (p.stdout + p.stderr)


def call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(SMALL_URL + path, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read())
        except Exception:
            raise StepError(f"{path} answered HTTP {e.code}")
    except Exception as e:
        raise StepError(f"{path} did not answer ({type(e).__name__}). Is the demo stack running?")
    if isinstance(out, dict) and "error" in out:
        raise StepError(f"{path} returned an error: {out['error']}")
    return out


# ---- demo stack -----------------------------------------------------------------------------------------------------------
def stack_up():
    rc, out = sh(["docker", "inspect", "-f", "{{.State.Running}}", SMALL], 20)
    if rc != 0 or out.strip() != "true":
        return False
    rc, _ = sh(["docker", "inspect", BIG], 20)
    if rc == 0:
        return False                                        # the round already ran: the aggregator is done
    try:
        h = call("/health")
    except StepError:
        return False
    return "small_test_patients" in json.dumps(h)


def stack_cmd(what):
    env = {k: v for k, v in os.environ.items() if k != "DEMO_NNPA"}      # the accelerator stays off
    rc, out = sh(["bash", str(ROOT / "demo/demo.sh"), what], 600, env)
    return rc, out


def prepare(background=True):
    def work():
        STACK.update(state="preparing", error=None)
        stack_cmd("down")
        rc, out = stack_cmd("prep")
        if rc == 0 and stack_up():
            STACK.update(state="ready")
        else:
            STACK.update(state="failed", error="demo stack did not start: " + " ".join(out.split())[-300:])
    if background:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()


def require_stack():
    if STACK["state"] == "preparing":
        raise StepError("the demo stack is still starting; wait a few seconds")
    if not stack_up():
        raise StepError("the demo stack is not ready (after the round it must be rebuilt: press R)")
    STACK["state"] = "ready"


# ---- steps ----------------------------------------------------------------------------------------------------------------
def step1():
    require_stack()
    r = call("/audit", {"model": "borrowed"})
    return {"rate": r["alerts_per_100_patient_days"], "patients": r["patients"], "patient_days": r["patient_days"],
            "threshold": r["threshold"], "budget": BUDGET}


def step2():
    if 1 not in RUN:
        raise StepError("run step 1 first")
    require_stack()
    t = call("/retune", {"model": "borrowed", "budget": BUDGET})
    a = call("/audit", {"model": "retuned"})
    b = RUN[1]["data"]
    return {"rate_before": b["rate"], "threshold_before": b["threshold"], "threshold_after": t["threshold"],
            "tune_rate": t["alerts_per_100_patient_days"], "tune_patients": t["patients"],
            "rate_after": a["alerts_per_100_patient_days"], "test_patients": a["patients"], "budget": BUDGET}


def docker_logs(name):
    rc, out = sh(["docker", "logs", name], 60)
    if rc != 0:
        raise StepError(f"could not read the log of {name}")
    return out


def step3():
    if 2 not in RUN:
        raise StepError("run steps 1 and 2 first")
    require_stack()
    t0 = time.monotonic()
    rc, out = sh(["bash", str(HERE / "round.sh")], 400)
    if rc != 0:
        raise StepError("the federated round failed: " + " ".join(out.split())[-300:])
    secs = round(time.monotonic() - t0, 1)
    log = docker_logs(AGG)
    seen, sites = {}, {}
    for line in log.splitlines():
        m = re.search(r"event=request_keys site=(\S+) round=\S+ path=(\S+) keys=(\S*)", line)
        if m:
            site, path, keys = m.group(1), m.group(2), tuple(sorted(filter(None, m.group(3).split(","))))
            sites.setdefault(site, {}).setdefault((path, keys), 0)
            sites[site][(path, keys)] += 1
            for k in keys:
                seen[k] = FIELD_TYPES.get(k)
    unexpected = sorted(k for k, v in seen.items() if v is None)
    ok = bool(seen) and not unexpected
    reason = None if ok else ("no request was logged by the aggregator" if not seen else
                              "unexpected fields reached the aggregator: " + ", ".join(unexpected))
    rounds = len(re.findall(r"event=round_complete", log))
    return {
        "seconds": secs, "rounds_completed": rounds, "done": "done=True" in log,
        "sites": [{"site": s, "messages": [{"path": p, "count": c, "fields": list(k),
                                            "types": sorted({FIELD_TYPES[f] for f in k if FIELD_TYPES.get(f)})}
                                           for (p, k), c in sorted(m.items())]} for s, m in sorted(sites.items())],
        "check": {"pass": ok, "patient_rows": 0 if ok else None, "reason": reason, "unexpected": unexpected},
    }


def step4():
    rows = []
    files = sorted(FED_RESULTS.glob("seed*_S1.csv"))
    if not files:
        raise StepError("no saved results found for the smallest-hospital comparison")
    for f in files:
        rows += list(csv.DictReader(open(f)))

    def pick(fraction, direction=None):
        got = {}
        for r in rows:
            if (r["fraction"] != str(fraction) or r["budget"] != str(BUDGET) or r["setup"] not in ("3", "4")
                    or (direction and r["direction"] != direction)):
                continue
            got.setdefault((r["seed"], r["direction"]), {})[r["setup"]] = (float(r["sensitivity"]), int(r["n_train_pat"]) + int(r["n_val_pat"]))
        pairs = [v for v in got.values() if "3" in v and "4" in v]
        if not pairs:
            raise StepError(f"saved results have no alone/together pairs for fraction {fraction}")
        alone, together = [p["3"][0] for p in pairs], [p["4"][0] for p in pairs]
        diffs = [b - a for a, b in zip(alone, together)]
        return {"runs": len(pairs), "alone": st.mean(alone), "together": st.mean(together), "gap_per_100": 100 * st.mean(diffs),
                "ahead_in": sum(d > 0 for d in diffs), "pool_patients": round(st.mean(p["3"][1] for p in pairs))}

    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode() + hashlib.sha256(f.read_bytes()).digest())
    return {"scenario": "S1 (all 138 features)", "budget": BUDGET, "direction": "big hospital B, small hospital A",
            "smallest": pick(0.01, "B>A"), "smallest_both_directions": pick(0.01), "larger": pick(0.1, "B>A"),
            "source": {"files": len(files), "sha256": h.hexdigest()[:16]}}


STEPS = {1: step1, 2: step2, 3: step3, 4: step4}


def run_step(n):
    with LOCK:
        t0 = time.monotonic()
        try:
            data = STEPS[n]()
        except StepError as e:
            return {"ok": False, "step": n, "error": str(e)}
        except Exception as e:                                          # fail loudly, never fake a result
            return {"ok": False, "step": n, "error": f"{type(e).__name__}: {e}"}
        res = {"ok": True, "step": n, "data": data, "seconds": data.get("seconds", round(time.monotonic() - t0, 1))}
        RUN[n] = res
        if all(k in RUN for k in (1, 2, 3, 4)):
            save_replay()
        return res


# ---- replay ---------------------------------------------------------------------------------------------------------------
def run_hash(steps):
    return hashlib.sha256(json.dumps(steps, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save_replay():
    steps = [RUN[k] for k in (1, 2, 3, 4)]
    now = datetime.now(timezone.utc)
    doc = {"format": 1, "recorded_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "one real live run of the pipeline demo",
           "run_sha256": run_hash(steps), "steps": steps}
    REPLAY_DIR.mkdir(exist_ok=True)
    p = REPLAY_DIR / f"replay-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    p.write_text(json.dumps(doc, indent=1))
    print(f"saved replay: {p.name}", file=sys.stderr)


def load_replay():
    files = sorted(REPLAY_DIR.glob("replay-*.json")) if REPLAY_DIR.is_dir() else []
    if not files:
        raise StepError("no saved replay yet: do one live run first")
    doc = json.loads(files[-1].read_text())
    if doc.get("run_sha256") != run_hash(doc.get("steps")):
        raise StepError("the saved replay does not match its hash; refusing to play it")
    if not all(s.get("ok") for s in doc["steps"]):
        raise StepError("the saved replay contains a failed step; refusing to play it")
    return doc


# ---- http -----------------------------------------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self.send((HERE / "index.html").read_bytes(), ctype="text/html; charset=utf-8")
        if p == "/api/state":
            return self.send({"mode": "replay-only" if ARGS.replay_only else "live", "stack": STACK["state"],
                              "stack_error": STACK["error"], "done": sorted(RUN)})
        if p == "/api/replay":
            try:
                return self.send({"ok": True, "replay": load_replay()})
            except StepError as e:
                return self.send({"ok": False, "error": str(e)})
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]
        m = re.fullmatch(r"/api/step/([1-4])", p)
        if m:
            if ARGS.replay_only:
                return self.send({"ok": False, "step": int(m.group(1)), "error": "this server is replay-only: use ?replay=1"})
            return self.send(run_step(int(m.group(1))))
        if p == "/api/reset":
            RUN.clear()
            if not ARGS.replay_only and not stack_up() and STACK["state"] != "preparing":
                prepare()
            return self.send({"ok": True, "stack": STACK["state"]})
        self.send({"error": "not found"}, 404)


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--prep", action="store_true", help="start the vid-* demo stack first")
    ap.add_argument("--keep", action="store_true", help="leave the vid-* stack running on exit")
    ap.add_argument("--replay-only", action="store_true", help="serve the page and the saved replay only")
    ARGS = ap.parse_args()
    if not ARGS.replay_only:
        if ARGS.prep:
            print("starting the demo stack (vid-*) ...", file=sys.stderr)
            prepare(background=False)
        else:
            STACK["state"] = "ready" if stack_up() else "down"
        print(f"demo stack: {STACK['state']}" + (f" ({STACK['error']})" if STACK["error"] else ""), file=sys.stderr)
        if STACK["state"] not in ("ready",):
            print("start it with: --prep   (only vid-* containers are touched)", file=sys.stderr)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print(f"open http://localhost:{ARGS.port}/" + ("?replay=1" if ARGS.replay_only else ""), file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if not ARGS.replay_only and not ARGS.keep:
            print("removing the demo stack (vid-* only) ...", file=sys.stderr)
            stack_cmd("down")


if __name__ == "__main__":
    main()
