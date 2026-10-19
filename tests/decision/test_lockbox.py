"""Lockbox simulation: one open position per symbol, portfolio at-risk cap, dollar P&L."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.decision.lockbox import evaluate_lockbox
from sgt.decision.policy import PolicyParams

LABEL = "y_net__tp2.0_sl1.0_h30"


class _Stub:
    """mu high for rows flagged good, so the policy trades exactly those."""

    def predict_frame(self, df):
        good = df["good"].to_numpy(dtype=bool)
        n = len(df)
        return {"mu": np.where(good, 0.05, -0.05), "sigma": np.full(n, 0.001),
                "p": np.where(good, 0.9, 0.1), "b": np.full(n, 2.0)}


def _df(rows):
    return pd.DataFrame(rows, columns=["ts", "session_date", "symbol", "good", LABEL,
                                       "cost_rt_measured"])


def _params(**kw):
    base = dict(k_tp=2.0, k_sl=1.0, n_eff=5000, z=1.0, p_min=0.5, f_max=0.02,
                portfolio_cap=0.06, fallback_round_trip=0.002)
    base.update(kw)
    return PolicyParams(**base)


def test_one_position_per_symbol_within_horizon():
    df = _df([
        ("2026-10-06T14:00Z", "2026-10-06", "AAA", True, 0.01, 0.002),
        ("2026-10-06T14:10Z", "2026-10-06", "AAA", True, 0.01, 0.002),   # inside 30m -> skip
        ("2026-10-06T14:31Z", "2026-10-06", "AAA", True, -0.02, 0.002),  # after -> take
        ("2026-10-06T14:05Z", "2026-10-06", "BBB", False, 0.50, 0.002),  # policy declines
    ])
    r = evaluate_lockbox(_Stub(), _params(), df, label_col=LABEL, horizon_min=30,
                         cost_col="cost_rt_measured", model_cost_col=None, equity=100_000)
    assert r.n_signals == 3 and r.n_trades == 2
    sizes = r.trades["size_fraction"].to_numpy()
    expected = float((sizes * 100_000 * np.array([0.01, -0.02])).sum())
    assert abs(r.pnl_usd - expected) < 1e-9
    assert r.hit_rate == 0.5


def test_portfolio_cap_blocks_concurrent_entries():
    rows = [(f"2026-10-06T14:0{i}Z", "2026-10-06", f"S{i}", True, 0.01, 0.002)
            for i in range(6)]
    p = _params(portfolio_cap=0.01, n_eff=100, f_fixed=0.005)   # fixed 0.5% -> 2 fit
    r = evaluate_lockbox(_Stub(), p, _df(rows), label_col=LABEL, horizon_min=30,
                         cost_col="cost_rt_measured", model_cost_col=None)
    assert r.n_signals == 6 and r.n_trades == 2


def test_empty_lockbox():
    r = evaluate_lockbox(_Stub(), _params(), _df([]), label_col=LABEL, horizon_min=30,
                         cost_col="cost_rt_measured", model_cost_col=None)
    assert r.n_trades == 0 and r.pnl_usd == 0.0


def test_zero_kelly_size_is_not_a_trade():
    """F-3: p*b < 1-p gives Kelly f* <= 0 -> size 0. Live declines it; so must validation."""
    class _BadPayoff(_Stub):
        def predict_frame(self, df):
            out = super().predict_frame(df)
            out["p"] = np.full(len(df), 0.6)
            out["b"] = np.full(len(df), 0.3)      # 0.6 - 0.4/0.3 < 0
            return out

    df = _df([("2026-10-06T14:00Z", "2026-10-06", "AAA", True, 0.01, 0.002)])
    r = evaluate_lockbox(_BadPayoff(), _params(n_eff=5000), df, label_col=LABEL,
                         horizon_min=30, cost_col="cost_rt_measured", model_cost_col=None)
    assert r.n_signals == 0 and r.n_trades == 0


def test_cluster_key_overrides_symbol_session_clusters():
    from sgt.decision.models import effective_n
    from sgt.decision.validation import cluster_ids

    df = pd.DataFrame({"symbol": ["A", "B", "C"], "session_date": ["d1", "d1", "d2"]})
    assert effective_n(df) == 3
    df["cluster_key"] = df["session_date"]
    assert effective_n(df) == 2 and list(cluster_ids(df)) == ["d1", "d1", "d2"]


def test_model_treats_infinite_features_as_missing():
    from sgt.decision.models import DecisionModel

    rng = np.random.default_rng(0)
    rows = []
    for d in range(40):
        for s in range(5):
            for _ in range(6):
                f = float(rng.normal())
                rows.append({"symbol": f"S{s}", "session_date": f"2026-01-{d % 28 + 1:02d}-{d}",
                             "f_a": f, "f_b": np.inf if rng.uniform() < 0.05 else float(rng.normal()),
                             LABEL: 0.02 * f + float(rng.normal(scale=0.01))})
    df = pd.DataFrame(rows)
    m = DecisionModel().fit(df, ["f_a", "f_b"], LABEL)          # must not raise
    p = m.predict({"f_a": 0.5, "f_b": float("inf")})
    assert np.isfinite(p.mu)
