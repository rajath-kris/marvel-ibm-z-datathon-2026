"""Unit tests for fedsecure + the aggregator's SECURE mode (standard library only).
Run: python3 -B tests/test_fedsecure.py"""
import math
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "aggregator"))

import fedsecure as fs  # noqa: E402
import aggregator  # noqa: E402
from aggregator import AggregatorState, ApiError, weighted_average, combine_totals  # noqa: E402

aggregator.log = lambda *a, **k: None
SITES = ["hospA1", "hospA2", "hospB"]
D = 138


def make_seeds(sites, rng):
    pair = {fs.pair_key(a, b): rng.randbytes(32) for i, a in enumerate(sites) for b in sites[i + 1:]}
    return {s: {p: pair[fs.pair_key(s, p)] for p in sites if p != s} for s in sites}


def site_updates(rng, ns=(313000, 313881, 52207)):
    return {s: {"w": [rng.gauss(0, 0.15) for _ in range(D)], "b": rng.gauss(-1.2, 0.05), "n": n}
            for s, n in zip(SITES, ns)}


def masked_update(site, seeds, rnd, u):
    vals = [u["n"] * x for x in u["w"]] + [u["n"] * u["b"]]
    m = fs.mask_vector(site, SITES, seeds[site], f"round-{rnd}", vals)
    return {"site_id": site, "round": rnd, "n": u["n"], "w": m[:-1], "b": m[-1]}


def ks_uniform(us):
    us = sorted(us)
    n = len(us)
    return max(max((i + 1) / n - u, u - i / n) for i, u in enumerate(us))


def corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))


class SecureEqualsPlain(unittest.TestCase):
    def test_masked_fedavg_equals_plain_fedavg(self):
        rng = random.Random(7)
        seeds = make_seeds(SITES, rng)
        worst = 0.0
        for trial in range(20):
            ups = site_updates(rng)
            plain = weighted_average(ups[s] for s in SITES)
            masked = [masked_update(s, seeds, trial, ups[s]) for s in SITES]
            total = sum(u["n"] for u in masked)
            nw = fs.unmask_sum(m["w"] for m in masked)
            nb = fs.unmask_sum([m["b"]] for m in masked)[0]
            sec = {"w": [x / total for x in nw], "b": nb / total}
            worst = max(worst, max(abs(x - y) for x, y in zip(sec["w"], plain["w"])), abs(sec["b"] - plain["b"]))
        print(f"\n    max |secure - plain FedAvg| over 20 rounds x {D + 1} values: {worst:.3e}")
        self.assertLess(worst, 1e-9)

    def test_aggregator_secure_mode_end_to_end(self):
        """Stats + 3 rounds through AggregatorState(secure=True) vs the plain AggregatorState."""
        rng = random.Random(11)
        seeds = make_seeds(SITES, rng)
        plain, sec = AggregatorState(SITES, 3), AggregatorState(SITES, 3, secure=True)
        for s in SITES:
            n = rng.randint(50000, 400000)
            ssum = [rng.uniform(-1, 1) * n * 80 for _ in range(D)]
            ssq = [abs(x) * 200 + n * 50 for x in ssum]
            plain.post_stats({"site_id": s, "n": n, "sum": ssum, "sumsq": ssq})
            m = fs.mask_vector(s, SITES, seeds[s], "stats", ssum + ssq)
            sec.post_stats({"site_id": s, "n": n, "sum": m[:D], "sumsq": m[D:]})
        npl, nse = plain.get_norm(), sec.get_norm()
        d_norm = max(max(abs(a - b) / max(abs(b), 1) for a, b in zip(nse[k], npl[k])) for k in ("mean", "std"))
        for r in range(3):
            ups = site_updates(rng)
            for s in SITES:
                plain.post_update(dict(ups[s], site_id=s, round=r))
                sec.post_update(masked_update(s, seeds, r, ups[s]))
        gp, gs = plain.get_global(), sec.get_global()
        d_w = max(abs(a - b) for a, b in zip(gs["model"]["w"], gp["model"]["w"]))
        print(f"\n    aggregator secure vs plain: mean/std rel diff {d_norm:.2e}, final w diff {d_w:.2e}, done={gs['done']}")
        self.assertTrue(gs["done"])
        self.assertLess(d_norm, 1e-12)
        self.assertLess(d_w, 1e-9)
        self.assertLess(abs(gs["model"]["b"] - gp["model"]["b"]), 1e-9)


class MaskedUpdateLooksRandom(unittest.TestCase):
    def test_single_masked_update_is_uniform_and_uncorrelated(self):
        rng = random.Random(3)
        seeds = make_seeds(SITES, rng)
        u = site_updates(rng)["hospB"]
        true = [u["n"] * x for x in u["w"]]
        m = masked_update("hospB", seeds, 0, u)["w"]
        us = [x / fs.MOD for x in m]
        ks = ks_uniform(us)
        r = corr(us, true)
        decoded = [fs.decode(x) for x in m]
        rel = max(abs(a - b) for a, b in zip(decoded, true)) / max(abs(x) for x in true)
        print(f"\n    one masked update (hospB, {D} values): KS vs uniform {ks:.3f} (1% critical {1.63 / math.sqrt(D):.3f}), "
              f"corr with true n*w {r:+.3f}, decoding it alone is off by {rel:.2e} x max|n*w|")
        self.assertLess(ks, 1.63 / math.sqrt(D))
        self.assertLess(abs(r), 0.25)
        self.assertGreater(rel, 1e6)            # decoding a single masked update gives garbage, not n*w

    def test_same_update_masked_differently_each_round(self):
        rng = random.Random(5)
        seeds = make_seeds(SITES, rng)
        u = site_updates(rng)["hospA1"]
        a, b = masked_update("hospA1", seeds, 0, u)["w"], masked_update("hospA1", seeds, 1, u)["w"]
        self.assertEqual(sum(x == y for x, y in zip(a, b)), 0)


    def test_masks_never_repeat_across_runs(self):
        """Same seeds, same round, same values, different session id -> no masked value repeats, so two runs'
        messages cannot be subtracted to cancel the masks (this leaked the update difference before the fix)."""
        rng = random.Random(17)
        seeds = make_seeds(SITES, rng)
        vals = [rng.gauss(0, 1) * 1e5 for _ in range(D)]
        a = fs.mask_vector("hospB", SITES, seeds["hospB"], "sessionA0000000|round-39", vals)
        b = fs.mask_vector("hospB", SITES, seeds["hospB"], "sessionB0000000|round-39", vals)
        diff = [fs.decode((x - y) % fs.MOD) for x, y in zip(a, b)]
        self.assertEqual(sum(x == y for x, y in zip(a, b)), 0)
        self.assertGreater(min(abs(d) for d in diff), 1e9)   # the difference is mask noise, not 0 (= vals - vals)


class Limits(unittest.TestCase):
    def test_missing_site_gives_garbage(self):
        """No dropout recovery: if one site's update is missing the masks do not cancel."""
        rng = random.Random(9)
        seeds = make_seeds(SITES, rng)
        ups = site_updates(rng)
        two = [masked_update(s, seeds, 0, ups[s]) for s in SITES[:2]]
        partial = fs.unmask_sum(m["w"] for m in two)
        true = [ups["hospA1"]["n"] * a + ups["hospA2"]["n"] * b for a, b in zip(ups["hospA1"]["w"], ups["hospA2"]["w"])]
        self.assertGreater(max(abs(x - y) for x, y in zip(partial, true)), 1e6)

    def test_wrong_seed_does_not_cancel(self):
        rng = random.Random(13)
        seeds = make_seeds(SITES, rng)
        seeds["hospB"]["hospA1"] = rng.randbytes(32)        # hospB disagrees with hospA1 about their seed
        ups = site_updates(rng)
        m = [masked_update(s, seeds, 0, ups[s]) for s in SITES]
        total = sum(u["n"] for u in m)
        sec = [x / total for x in fs.unmask_sum(x["w"] for x in m)]
        plain = weighted_average(ups[s] for s in SITES)["w"]
        self.assertGreater(max(abs(a - b) for a, b in zip(sec, plain)), 1.0)

    def test_encode_range_and_roundtrip(self):
        for x in (0.0, -1.5, 3.25e9, -7.1e-6, 2.5e10 * 627000):
            self.assertLess(abs(fs.decode(fs.encode(x)) - x), 1e-12 + abs(x) * 1e-15)
        with self.assertRaises(ValueError):
            fs.encode(1e30)

    def test_secure_aggregator_rejects_plain_floats_and_mixed_state(self):
        sec = AggregatorState(SITES, 2, secure=True)
        with self.assertRaises(ApiError) as cm:
            sec.post_stats({"site_id": "hospB", "n": 10, "sum": [1.5] * D, "sumsq": [2.5] * D})
        self.assertEqual(cm.exception.status, 400)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            AggregatorState(SITES, 2, p).post_stats({"site_id": "hospB", "n": 1, "sum": [1.0], "sumsq": [1.0]})
            with self.assertRaises(ValueError):
                AggregatorState(SITES, 2, p, secure=True)     # plain state must not be resumed as secure

    def test_pair_seed_files(self):
        with tempfile.TemporaryDirectory() as d:
            for a, b in (("hospA1", "hospA2"), ("hospA1", "hospB"), ("hospA2", "hospB")):
                with open(os.path.join(d, fs.pair_key(a, b) + ".seed"), "w") as f:
                    f.write(os.urandom(32).hex())
            self.assertEqual(sorted(fs.load_pair_seeds(d, "hospB")), ["hospA1", "hospA2"])
            self.assertEqual(fs.load_pair_seeds(d, "hospA2")["hospB"], fs.load_pair_seeds(d, "hospB")["hospA2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
