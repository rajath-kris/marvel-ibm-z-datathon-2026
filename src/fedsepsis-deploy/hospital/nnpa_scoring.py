"""Optional NNPA scoring for the hospital service (off unless NNPA_SCORE=1).

What it does: the fedsepsis library scores rows with `z @ w` (standardised rows x weight vector) in float64.
install() makes ONLY that product run on the IBM Z NNPA accelerator (torch device "nnpa"), for the scoring
functions, and nothing else. The library file is not modified; training (local_train) is never touched.

How: the library calls `_std_chunk` just before `z @ w`. While one of the wrapped scoring functions runs
IN THIS THREAD, `_std_chunk` returns an ndarray subclass whose `@` goes to the accelerator. Other threads
(the training loop, other requests) see the unmodified library. Everything else (standardising, clipping,
sigmoid, alert rule, threshold search) is the library's own code.

NNPA_SCOPE=patient (default): `_scores`, i.e. score_patient -> /score and /patient.
NNPA_SCOPE=all: also `_patient_scores`, i.e. alert_rate (/audit) and threshold_for_budget (/retune).
NNPA_MIN_ROWS (default 0): blocks with fewer rows stay on the CPU.

Safety: if torch/NNPA is missing, the self-test fails, or any call errors or returns a non-finite value, the CPU
product is used instead (counted in stats()["fallbacks"]); the service never fails because of the accelerator.

Precision: NNPA computes in a 16-bit format (DLFloat16). Scores therefore differ slightly from the CPU path;
tests/test_nnpa_parity.py measures by how much on the real data. The CPU path stays the default and the reference.
"""
import os
import threading
import time

import numpy as np

_tl = threading.local()
_lock = threading.Lock()
_S = {"enabled": False, "scope": None, "min_rows": 0, "nnpa_calls": 0, "nnpa_rows": 0, "nnpa_seconds": 0.0,
      "cpu_calls": 0, "fallbacks": 0, "last_error": None}
_torch = None
_log = None


def stats():
    return dict(_S, nnpa_seconds=round(_S["nnpa_seconds"], 4))


class _NZ(np.ndarray):
    """float64 standardised chunk whose `@ weight_vector` runs on the NNPA."""

    def __matmul__(self, other):
        z = self.view(np.ndarray)
        w = np.asarray(other)
        if not (_S["enabled"] and z.ndim == 2 and w.ndim == 1 and z.shape[0] >= _S["min_rows"]):
            _S["cpu_calls"] += 1
            return z @ w
        try:
            t0 = time.perf_counter()
            with _lock:
                zt = _torch.from_numpy(z.astype(np.float32)).to("nnpa")
                wt = _torch.from_numpy(w.astype(np.float32)).reshape(-1, 1).to("nnpa")
                r = (zt @ wt).cpu().numpy().reshape(-1).astype(np.float64)   # (n,d)@(d,1): the shape the NNPA runs
            if not np.isfinite(r).all():
                raise ValueError("non-finite NNPA result")
            _S["nnpa_calls"] += 1
            _S["nnpa_rows"] += int(z.shape[0])
            _S["nnpa_seconds"] += time.perf_counter() - t0
            return r
        except Exception as e:
            _S["fallbacks"] += 1
            _S["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
            if _log:
                _log("nnpa_fallback", reason=_S["last_error"], scoring="cpu_for_this_call")
            return z @ w


def _wrap(orig, name):
    def run(*a, **k):
        before = _S["nnpa_rows"]
        _tl.depth = getattr(_tl, "depth", 0) + 1
        try:
            return orig(*a, **k)
        finally:
            _tl.depth -= 1
            if _log and _tl.depth == 0:
                rows = _S["nnpa_rows"] - before
                _log("nnpa_scored", fn=name, scored_on="nnpa" if rows else "cpu", nnpa_rows=rows)
    run.__name__ = name
    run.__wrapped__ = orig
    return run


def install(fs, log=None):
    """Patch the fedsepsis module object `fs`. Returns True if scoring now uses the NNPA, False if it stays on the CPU."""
    global _torch, _log
    _log = log
    scope = os.environ.get("NNPA_SCOPE", "patient")
    try:
        if scope not in ("patient", "all"):
            raise ValueError("NNPA_SCOPE must be patient or all, got %r" % scope)
        import torch
        if not torch.nnpa.is_available():
            raise RuntimeError("torch.nnpa.is_available() is False")
        rng = np.random.default_rng(0)
        z, w = rng.normal(size=(256, 138)), rng.normal(scale=0.1, size=138)
        r = (torch.from_numpy(z.astype(np.float32)).to("nnpa") @ torch.from_numpy(w.astype(np.float32)).reshape(-1, 1).to("nnpa"))
        if not str(r.device).startswith("nnpa"):
            raise RuntimeError("self-test result is on %s, not nnpa" % r.device)
        err = float(np.abs(r.cpu().numpy().reshape(-1) - z @ w).max())
        if not err < 0.5:
            raise RuntimeError("self-test error %.3g too large" % err)
        _torch = torch
    except Exception as e:
        _S["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        if log:
            log("nnpa_fallback", reason=_S["last_error"], scoring="cpu")
        return False
    _S["min_rows"] = int(os.environ.get("NNPA_MIN_ROWS", "0"))
    orig_std = fs._std_chunk

    def std_chunk(Xc, mean, inv_std):
        z = orig_std(Xc, mean, inv_std)
        return z.view(_NZ) if getattr(_tl, "depth", 0) > 0 else z

    fs._std_chunk = std_chunk
    fs._scores = _wrap(fs._scores, "_scores")
    if scope == "all":
        fs._patient_scores = _wrap(fs._patient_scores, "_patient_scores")
    _S.update(enabled=True, scope=scope)
    if log:
        log("nnpa_enabled", scope=scope, min_rows=_S["min_rows"], torch=torch.__version__,
            telum2=bool(torch.nnpa.is_telum2_available()), selftest_max_err="%.2e" % err)
    return True
