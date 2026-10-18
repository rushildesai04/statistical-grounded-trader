"""Training + validation tests (DECISION_MODEL.md §8, §10): verdicts, CPCV purge,
gates, trial ledger / DSR."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.db.session import get_session
from sgt.decision import trial_ledger
from sgt.decision.models import DecisionModel
from sgt.decision.policy import PolicyParams
from sgt.decision.train import (
    TrainConfig,
    discover_labels,
    train_decision_model,
)
from sgt.decision.validation import (
    cost_stress_sign_stable,
    deflated_sharpe_gate,
    policy_trade_returns,
    session_cpcv,
    sign_consistent_positive,
)

LABEL = "y_net__tp2.0_sl1.0_h12"


def _dataset(n_sessions=50, n_symbols=5, per=8, seed=0, signal=0.02, noise=0.01,
             leaky=False):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_sessions):
        sess = f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}"
        for s in (f"S{i}" for i in range(n_symbols)):
            for _ in range(per):
                f0 = float(rng.normal())
                y = signal * f0 - 0.004 + float(rng.normal(scale=noise))
                row = {"symbol": s, "ts": f"{sess}T10:00", "session_date": sess,
                       "trigger": "ema_cross", "cost_rt_measured": 0.004,
                       "discovered_session": "2026-01-01", "f_sig": f0,
                       "f_n0": float(rng.normal()), "f_n1": float(rng.normal()),
                       LABEL: y, "uniq_w__tp2.0_sl1.0_h12": 1.0}
                if leaky:
                    row["f_leak"] = y
                rows.append(row)
    return pd.DataFrame(rows)


def _tc(**kw):
    base = dict(run_id="t", z_grid=(1.0,), p_min_grid=(0.5,))
    base.update(kw)
    return TrainConfig(**base)


def test_discover_labels():
    specs = discover_labels(["f_x", LABEL, "uniq_w__tp2.0_sl1.0_h12", "other"])
    assert len(specs) == 1
    s = specs[0]
    assert (s.k_tp, s.k_sl, s.H) == (2.0, 1.0, 12) and s.uniq_col == "uniq_w__tp2.0_sl1.0_h12"


def test_planted_signal_promotes():
    df = _dataset(seed=1, signal=0.02, noise=0.01)
    with get_session() as s:
        r = train_decision_model(df, _tc(run_id="promote"), session=s)
    assert r.verdict == "PROMOTE-CANDIDATE"
    assert all(g.passed for g in r.gates)
    assert r.model is not None and r.n_trials >= trial_ledger.PRIOR_TOTAL


def test_pure_noise_is_no_trade():
    df = _dataset(seed=2, signal=0.0, noise=0.02)
    with get_session() as s:
        r = train_decision_model(df, _tc(run_id="noise"), session=s)
    assert r.verdict == "NO-TRADE"


def test_contamination_invalidates_run():
    # >300 clusters -> GBM tier (can memorize); overlapping train/test sessions = leak.
    df = _dataset(n_sessions=40, n_symbols=8, per=5, seed=3, signal=0.0, noise=0.02,
                  leaky=True)
    sess = sorted(df["session_date"].unique())
    contaminated = ((tuple(sess), tuple(sess[: len(sess) // 2])),)
    with get_session() as s:
        r = train_decision_model(df, _tc(run_id="contam", splits_override=contaminated),
                                 session=s, fit_final=False)
    assert r.verdict == "INVALID"
    assert not r.shuffle_null["is_null"]


def test_cpcv_never_trains_on_test_or_embargo():
    sessions = [f"d{i:02d}" for i in range(12)]
    splits = session_cpcv(sessions, n_groups=4, k_test=1, embargo=1)
    assert splits
    pos = {s: i for i, s in enumerate(sessions)}
    for train_s, test_s in splits:
        assert not (set(train_s) & set(test_s))  # no test session in train
        for ts in test_s:                        # embargo: no adjacent session in train
            assert all(abs(pos[tr] - pos[ts]) > 1 for tr in train_s)


def test_cpcv_embargo_must_be_positive():
    with pytest.raises(ValueError):
        session_cpcv(["a", "b", "c", "d"], 3, 1, embargo=0)


def test_per_fold_sign_predicate():
    assert sign_consistent_positive([0.01, 0.02, 0.005])
    assert not sign_consistent_positive([0.01, -0.002, 0.004])  # sign flip
    assert not sign_consistent_positive([])


def test_cost_stress_sign_flip_rejected():
    assert cost_stress_sign_stable({0.5: 0.01, 1.0: 0.005, 1.5: 0.002})
    assert not cost_stress_sign_stable({0.5: 0.01, 1.0: 0.001, 1.5: -0.003})  # flip
    assert not cost_stress_sign_stable({0.5: 0.0, 1.0: 0.0, 1.5: 0.0})


def test_cost_multiplier_shifts_returns():
    df = _dataset(seed=4, signal=0.02, noise=0.01)
    feats = [c for c in df.columns if c.startswith("f_")]
    model = DecisionModel().fit(df, feats, LABEL, weight_col="uniq_w__tp2.0_sl1.0_h12")
    pp = PolicyParams(k_tp=2.0, k_sl=1.0, n_eff=model.n_eff, z=1.0, h=0.0, p_min=0.5)
    cheap, _ = policy_trade_returns(model, pp, df, label_col=LABEL,
                                    cost_col="cost_rt_measured", cost_mult=0.5)
    dear, _ = policy_trade_returns(model, pp, df, label_col=LABEL,
                                   cost_col="cost_rt_measured", cost_mult=1.5)
    assert cheap.size == dear.size and cheap.sum() > dear.sum()  # more cost -> less P&L


def test_trial_ledger_counts_and_feed_dsr():
    with get_session() as s:
        assert trial_ledger.seed_prior_trials(s) == trial_ledger.PRIOR_TOTAL
        assert trial_ledger.seed_prior_trials(s) == 0  # idempotent
        assert trial_ledger.trial_count(s) == trial_ledger.PRIOR_TOTAL
        trial_ledger.record_trial(s, run_id="r", params={"z": 1.0}, metrics={"sharpe": 0.2})
        trial_ledger.record_trial(s, run_id="r", params={"z": 2.0}, metrics={"sharpe": 0.4})
        assert trial_ledger.trial_count(s) == trial_ledger.PRIOR_TOTAL + 2
        var = trial_ledger.sharpe_variance_across_trials(s, "r")
        assert var is not None and var > 0


def test_more_trials_deflate_sharpe():
    returns = np.full(400, 0.01) + np.random.default_rng(0).normal(scale=0.02, size=400)
    dsr_few, _ = deflated_sharpe_gate(returns, n_trials=1, sr_variance=0.001)
    dsr_many, _ = deflated_sharpe_gate(returns, n_trials=500, sr_variance=0.001)
    assert dsr_many <= dsr_few


# ------------------------------------------------------------------ F2: model-cost fallback

def test_model_cost_used_when_measured_missing():
    # No measured quotes at all; the per-row cost must fall back to cost_rt_model (not 0),
    # so the +/-50% stress still shifts P&L. On the old code this collapsed to 0 (vacuous).
    df = _dataset(seed=4, signal=0.02, noise=0.01)
    df["cost_rt_measured"] = np.nan
    df["cost_rt_model"] = 0.004
    feats = [c for c in df.columns if c.startswith("f_")]
    model = DecisionModel().fit(df, feats, LABEL, weight_col="uniq_w__tp2.0_sl1.0_h12")
    pp = PolicyParams(k_tp=2.0, k_sl=1.0, n_eff=model.n_eff, z=1.0, h=0.0, p_min=0.5)
    cheap, _ = policy_trade_returns(model, pp, df, label_col=LABEL, cost_col="cost_rt_measured",
                                    model_cost_col="cost_rt_model", cost_mult=0.5)
    dear, _ = policy_trade_returns(model, pp, df, label_col=LABEL, cost_col="cost_rt_measured",
                                   model_cost_col="cost_rt_model", cost_mult=1.5)
    assert cheap.size == dear.size and cheap.size > 0 and cheap.sum() > dear.sum()


def test_verdict_capped_when_measured_cost_coverage_low():
    df = _dataset(seed=1, signal=0.02, noise=0.01)  # the dataset that promotes
    df["cost_rt_measured"] = np.nan                 # 0% measured coverage
    df["cost_rt_model"] = 0.004
    with get_session() as s:
        r = train_decision_model(df, _tc(run_id="unverified"), session=s)
    assert r.verdict == "PROMOTE-CANDIDATE-UNVERIFIED-COSTS"
    assert r.coverage["measured_cost_coverage"] == 0.0


# ------------------------------------------------------------------ F3: per-fold n_eff

def test_fold_n_eff_from_training_sessions_only(monkeypatch):
    import sgt.decision.train as train_mod

    df = _dataset(seed=1, signal=0.02, noise=0.01)
    total = df["session_date"].nunique()
    seen: list[int] = []
    real = train_mod.effective_n

    def spy(frame, *a, **k):
        seen.append(frame["session_date"].nunique())
        return real(frame, *a, **k)

    monkeypatch.setattr(train_mod, "effective_n", spy)
    with get_session() as s:
        train_decision_model(df, _tc(run_id="foldneff"), session=s, fit_final=False)
    assert max(seen) == total                      # the global n_eff sees every session
    assert any(c < total for c in seen)            # each fold's n_eff sees a training subset


# ------------------------------------------------------------------ F5: data-size-aware null

def test_null_auc_tolerance_is_data_size_aware():
    from sgt.decision.validation import is_null_result, null_auc_tolerance

    # Large sample: band collapses to the 0.03 floor, so AUC 0.55 is clearly above null.
    assert not is_null_result(0.55, 0.0, n_pos=10_000, n_neg=10_000)
    # Small sample: the band widens past 0.05, so the same AUC reads as null.
    assert is_null_result(0.55, 0.0, n_pos=25, n_neg=25)
    assert null_auc_tolerance(0.6, 10_000, 10_000) == pytest.approx(0.03)
    assert null_auc_tolerance(0.5, 25, 25) > 0.03


def test_truncated_nan_labels_are_excluded_not_fatal():
    """2026-10-18 regression: real datasets carry NaN labels for session-truncated trades;
    fitting/scoring must drop them (never impute) instead of crashing."""
    import numpy as np
    import pandas as pd

    from sgt.decision.validation import labeled_rows

    df = pd.DataFrame({"y": [0.01, np.nan, -0.02, np.inf], "f": [1, 2, 3, 4]})
    out = labeled_rows(df, "y")
    assert list(out["f"]) == [1, 3]


def test_training_runs_end_to_end_with_truncated_labels():
    import numpy as np

    df = _dataset(seed=3)
    label_cols = [c for c in df.columns if c.startswith("y_net__")]
    rng = np.random.default_rng(0)
    for c in label_cols:
        df.loc[rng.random(len(df)) < 0.05, c] = np.nan   # ~5% session-truncated
    with get_session() as s:
        r = train_decision_model(df, _tc(run_id="nanlabels"), session=s)
    assert r.verdict in {"PROMOTE-CANDIDATE", "PROMOTE-CANDIDATE-UNVERIFIED-COSTS", "NO-TRADE", "INVALID"}
    assert np.isfinite(r.expectancy)


def test_trial_ledger_commits_each_trial_immediately():
    """DB-2 regression: trials must be durable and must not hold the write lock."""
    from sgt.db.session import get_session
    from sgt.decision import trial_ledger

    with get_session() as s1:
        trial_ledger.record_trial(s1, run_id="durable", params={"a": 1}, metrics={})
        # A second, independent session must already see it while s1 is still open.
        with get_session() as s2:
            assert trial_ledger.trial_count(s2, "durable") == 1
