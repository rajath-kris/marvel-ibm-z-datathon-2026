"""Unit tests for the aggregator logic (no HTTP). Run: python -m unittest -v tests/test_aggregator_logic.py"""
import json
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "aggregator"))
sys.path.insert(0, os.path.join(HERE, "..", "stub"))

import aggregator  # noqa: E402
from aggregator import AggregatorState, ApiError  # noqa: E402
import fedsepsis as stub  # noqa: E402

aggregator.log = lambda *a, **k: None  # keep unit-test output readable; the event log is tested live


def ready_state(state_path=None, rounds=3):
    st = AggregatorState(["hospA", "hospB"], rounds, state_path)
    st.post_stats({"site_id": "hospA", "n": 100, "sum": [100.0, 200.0], "sumsq": [200.0, 500.0]})
    st.post_stats({"site_id": "hospB", "n": 300, "sum": [900.0, 1200.0], "sumsq": [3000.0, 5000.0]})
    return st


class OneRoundArithmetic(unittest.TestCase):
    def test_b_weighted_average_is_2_5_and_3_5(self):
        st = ready_state()
        st.post_update({"site_id": "hospA", "round": 0, "w": [1, 2], "b": 0.0, "n": 100})
        st.post_update({"site_id": "hospB", "round": 0, "w": [3, 4], "b": 0.0, "n": 300})
        g = st.get_global()
        self.assertEqual(g["round"], 1)
        self.assertEqual(g["model"]["w"], [2.5, 3.5])

    def test_stub_fedavg_agrees_with_aggregator(self):
        ups = [{"w": [1.0, 2.0], "b": 0.5, "n": 100}, {"w": [3.0, 4.0], "b": 1.5, "n": 300}]
        self.assertEqual(stub.fedavg_aggregate(ups), {"w": [2.5, 3.5], "b": 1.25})

    def test_norm_from_totals(self):
        n = ready_state().get_norm()
        self.assertTrue(n["ready"])
        self.assertEqual(n["mean"], [2.5, 3.5])
        self.assertAlmostEqual(n["std"][0], math.sqrt(1.75))
        self.assertAlmostEqual(n["std"][1], math.sqrt(1.5))

    def test_std_floor(self):
        st = AggregatorState(["a"], 1)
        st.post_stats({"site_id": "a", "n": 4, "sum": [8.0], "sumsq": [16.0]})  # constant feature
        self.assertEqual(st.get_norm()["std"], [1e-8])

    def test_stub_combine_stats_agrees_with_aggregator(self):
        X = [[1.0, 2.0], [3.0, 5.0], [2.0, 2.0], [7.0, 1.0]]
        parts = [stub.local_stats(X[:1]), stub.local_stats(X[1:])]
        st = AggregatorState(["a", "b"], 1)
        st.post_stats(dict(parts[0], site_id="a"))
        st.post_stats(dict(parts[1], site_id="b"))
        want = stub.combine_stats(parts)
        got = st.get_norm()
        for j in range(2):
            self.assertAlmostEqual(got["mean"][j], want["mean"][j])
            self.assertAlmostEqual(got["std"][j], want["std"][j])


class IdempotencyAndOrdering(unittest.TestCase):
    def test_d_duplicate_update_not_double_counted(self):
        st = ready_state()
        self.assertEqual(st.post_update({"site_id": "hospA", "round": 0, "w": [1, 2], "b": 0, "n": 100}), {"ok": True})
        dup = st.post_update({"site_id": "hospA", "round": 0, "w": [100, 100], "b": 0, "n": 100000})
        self.assertEqual(dup, {"ok": True, "duplicate": True})
        self.assertEqual(st.get_status()["sites_updated_this_round"], ["hospA"])
        self.assertEqual(st.get_global()["round"], 0)  # one site twice must not close the round
        st.post_update({"site_id": "hospB", "round": 0, "w": [3, 4], "b": 0, "n": 300})
        self.assertEqual(st.get_global()["model"]["w"], [2.5, 3.5])  # first hospA update kept

    def test_d_wrong_round_is_409(self):
        st = ready_state()
        for bad in (1, -1, 7):
            with self.assertRaises(ApiError) as cm:
                st.post_update({"site_id": "hospA", "round": bad, "w": [1, 2], "b": 0, "n": 1})
            self.assertEqual(cm.exception.status, 409)

    def test_done_after_expected_rounds(self):
        st = ready_state(rounds=2)
        for r in range(2):
            for s in ("hospA", "hospB"):
                st.post_update({"site_id": s, "round": r, "w": [1, 1], "b": 0, "n": 10})
        g = st.get_global()
        self.assertEqual((g["round"], g["done"]), (2, True))
        with self.assertRaises(ApiError) as cm:
            st.post_update({"site_id": "hospA", "round": 2, "w": [1, 1], "b": 0, "n": 10})
        self.assertEqual(cm.exception.status, 409)

    def test_unknown_site_and_bad_dims(self):
        st = ready_state()
        with self.assertRaises(ApiError) as cm:
            st.post_update({"site_id": "evil", "round": 0, "w": [1, 2], "b": 0, "n": 1})
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(ApiError) as cm:
            st.post_update({"site_id": "hospA", "round": 0, "w": [1, 2, 3], "b": 0, "n": 1})
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(ApiError) as cm:
            st.post_update({"site_id": "hospA", "round": 0, "w": [float("nan"), 1], "b": 0, "n": 1})
        self.assertEqual(cm.exception.status, 400)


class Persistence(unittest.TestCase):
    def test_g_restore_mid_round(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            st = ready_state(p)
            st.post_update({"site_id": "hospA", "round": 0, "w": [1, 2], "b": 0, "n": 100})
            st2 = AggregatorState(["hospB", "hospA"], 3, p)  # site order must not matter
            self.assertTrue(st2.restored)
            self.assertEqual(st2.get_status()["sites_updated_this_round"], ["hospA"])
            st2.post_update({"site_id": "hospB", "round": 0, "w": [3, 4], "b": 0, "n": 300})
            self.assertEqual(st2.get_global()["model"]["w"], [2.5, 3.5])
            with open(p) as f:
                self.assertEqual(json.load(f)["round"], 1)

    def test_refuses_state_from_a_different_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            ready_state(p)
            with self.assertRaises(ValueError):
                AggregatorState(["hospA", "hospC"], 3, p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
