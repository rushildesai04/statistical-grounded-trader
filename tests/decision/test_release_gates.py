"""Fresh-review (pass 2) regressions R2-F1..F5 + model-strategy load wiring (2026-10-18).

F1 quarantined / ineligible artifacts never trade; LIVE refuses the research override.
F2 entries stop at close - max(session_end buffer, horizon H).
F3 a degenerate outer fold is skipped and reported, never fatal.
F4 a concurrent duplicate trial insert is idempotent (IntegrityError -> False).
F5 a silent non-WAL journal mode is logged.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from sgt.core.types import Bar, Mode, Segment
from sgt.decision.features import CostModelAdapter
from sgt.decision.models import DecisionModel
from sgt.decision.policy import (
    EnsembleModelPolicy,
    PolicyParams,
    load_model_policy,
    policy_from_config,
)
from sgt.decision.runtime import DecisionRuntime
from sgt.strategy.loader import StrategyValidationError, load_strategy
from sgt.strategy.schema import ModelStrategyConfig

LABEL = "y_net__tp2.0_sl1.0_h12"


def _df(n_sessions=40, n_symbols=4, per=6, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_sessions):
        sess = f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}"
        for s in (f"S{i}" for i in range(n_symbols)):
            for _ in range(per):
                f0 = float(rng.normal())
                rows.append({"symbol": s, "session_date": sess, "f_sig": f0,
                             "f_n0": float(rng.normal()), "cost_rt_measured": 0.004,
                             LABEL: 0.02 * f0 - 0.004 + float(rng.normal(scale=0.01))})
    return pd.DataFrame(rows)


def _artifact(tmp_path, *, verdict="PROMOTE-CANDIDATE", eligible=True, name="m.joblib"):
    df = _df()
    m = DecisionModel().fit(df, ["f_sig", "f_n0"], LABEL)
    m.set_release_metadata(verdict=verdict, promotion_eligible=eligible, run_id="t")
    return m.save(tmp_path / name), m


def _params(**kw):
    base = dict(k_tp=2.0, k_sl=1.0, n_eff=100)
    base.update(kw)
    return PolicyParams(**base)


def _model_yaml(tmp_path, artifact, **model_over) -> str:
    model = {"artifact_path": str(artifact), "z": 0.5, "p_min": 0.55,
             "max_size_fraction": 0.01, **model_over}
    import yaml

    raw = {"id": "model_t", "kind": "model",
           "universe": {"float_max": 1e12, "price_min": 0.1},
           "triggers": ["hod_break"],
           "bracket": {"k_tp": 2.0, "k_sl": 1.0, "time_stop_minutes": 12},
           "sizing": {"risk_per_trade_pct_of_allocation": 0.5, "allocation_usd": 10_000.0,
                      "max_notional_usd": 20_000.0},
           "model": model}
    p = tmp_path / "model_t.yaml"
    p.write_text(yaml.safe_dump(raw))
    return str(p)


# ------------------------------------------------------------------ F1 artifact gate

def test_eligible_artifact_loads(tmp_path):
    path, _ = _artifact(tmp_path)
    assert isinstance(EnsembleModelPolicy.from_artifact(path, _params()), EnsembleModelPolicy)


@pytest.mark.parametrize("verdict,eligible", [
    ("RESEARCH-PROMISING", False),      # research dataset: never promotable
    ("PROMOTE-CANDIDATE", False),       # good verdict but dataset not eligible
    ("NO-TRADE", True),                 # eligible dataset but no edge
    ("INVALID", True),
])
def test_ineligible_artifact_refused(tmp_path, verdict, eligible):
    path, _ = _artifact(tmp_path, verdict=verdict, eligible=eligible)
    with pytest.raises(ValueError, match="F1"):
        EnsembleModelPolicy.from_artifact(path, _params())


def test_quarantine_filename_refused_even_with_eligible_metadata(tmp_path):
    path, _ = _artifact(tmp_path, name="m.quarantine.joblib")
    with pytest.raises(ValueError, match="quarantined=True"):
        EnsembleModelPolicy.from_artifact(path, _params())


def test_research_override_offline_only(tmp_path):
    path, _ = _artifact(tmp_path, verdict="RESEARCH-PROMISING", eligible=False,
                        name="r.quarantine.joblib")
    assert load_model_policy(path, _params(), allow_research_artifact=True, live=False)
    with pytest.raises(ValueError, match="refused in LIVE"):
        load_model_policy(path, _params(), allow_research_artifact=True, live=True)


def test_missing_metadata_fails_closed(tmp_path):
    df = _df()
    m = DecisionModel().fit(df, ["f_sig", "f_n0"], LABEL)   # never stamped
    path = m.save(tmp_path / "unstamped.joblib")
    with pytest.raises(ValueError, match="F1"):
        load_model_policy(path, _params())


# ------------------------------------------------------------------ model YAML wiring

def test_load_strategy_dispatches_kind_model(tmp_path):
    path, _ = _artifact(tmp_path)
    cfg = load_strategy(_model_yaml(tmp_path, path))
    assert isinstance(cfg, ModelStrategyConfig) and cfg.triggers == ["hod_break"]


def test_load_strategy_rejects_bad_model_yaml(tmp_path):
    import yaml

    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"id": "b", "kind": "model", "triggers": ["nope"]}))
    with pytest.raises(StrategyValidationError):
        load_strategy(str(p))


def test_rule_strategies_still_load():
    from sgt.strategy.schema import StrategyConfig

    assert isinstance(load_strategy("strategies/trader_v0.yaml"), StrategyConfig)


def test_policy_from_config_maps_params(tmp_path):
    path, m = _artifact(tmp_path)
    cfg = load_strategy(_model_yaml(tmp_path, path))
    pol = policy_from_config(cfg, live=True)
    p = pol._p
    assert (p.k_tp, p.k_sl, p.z, p.p_min, p.f_max) == (2.0, 1.0, 0.5, 0.55, 0.01)
    assert p.n_eff == m.n_eff > 0


def test_policy_from_config_live_refuses_research(tmp_path):
    path, _ = _artifact(tmp_path, verdict="RESEARCH-PROMISING", eligible=False)
    cfg = load_strategy(_model_yaml(tmp_path, path))
    with pytest.raises(ValueError, match="F1"):
        policy_from_config(cfg, live=True)
    with pytest.raises(ValueError, match="refused in LIVE"):
        policy_from_config(cfg, live=True, allow_research_artifact=True)
    assert policy_from_config(cfg, live=False, allow_research_artifact=True)


def test_policy_from_config_missing_artifact(tmp_path):
    cfg = load_strategy(_model_yaml(tmp_path, tmp_path / "nope.joblib"))
    with pytest.raises(FileNotFoundError):
        policy_from_config(cfg, live=False)


def _live_runner(cfg_path, mode, **kw):
    from sgt.accounts.registry import AccountSpec
    from sgt.brokers.costs import DEFAULT_COST_MODEL
    from sgt.brokers.sim import SimBroker
    from sgt.engine.backtest import default_risk_config
    from sgt.live.replay import InMemoryLiveSource
    from sgt.live.runner import LiveRunner

    async def _sleep(_s):
        return None

    spec = AccountSpec("acct", "sim", "cash", True, 1.0, 0.0)
    return LiveRunner(
        cfg_path, mode, [spec], source=InMemoryLiveSource([]),
        adapters={"acct": SimBroker("acct", DEFAULT_COST_MODEL, 100_000.0)},
        risk_config=default_risk_config(load_strategy(cfg_path)),
        clock=lambda: datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc), sleep=_sleep,
        run_id=f"run-model-{mode.value}", **kw)


def test_live_runner_builds_model_policy(tmp_path):
    path, _ = _artifact(tmp_path)
    r = _live_runner(_model_yaml(tmp_path, path), Mode.SHADOW)
    assert isinstance(r.engine._policy, EnsembleModelPolicy)


def test_live_runner_live_mode_refuses_research_artifact(tmp_path):
    path, _ = _artifact(tmp_path, verdict="RESEARCH-PROMISING", eligible=False)
    cfg_path = _model_yaml(tmp_path, path)
    with pytest.raises(ValueError, match="F1"):
        _live_runner(cfg_path, Mode.LIVE)
    with pytest.raises(ValueError, match="refused in LIVE"):
        _live_runner(cfg_path, Mode.LIVE, allow_research_artifact=True)
    # shadow may inspect a research artifact, but only when explicitly asked
    with pytest.raises(ValueError, match="F1"):
        _live_runner(cfg_path, Mode.SHADOW)
    assert _live_runner(cfg_path, Mode.SHADOW, allow_research_artifact=True).engine._policy


# ------------------------------------------------------------------ F2 entry cutoff

class _AlwaysTrade:
    def decide(self, features, context):
        from sgt.decision.runtime import PolicyDecision

        return PolicyDecision(True, 0.01, context.close * 0.9, context.close * 1.1, "t", {})


def test_entry_cutoff_uses_horizon_not_just_session_buffer():
    """2024-03-12 RTH closes 20:00 UTC. With H=120 min no entry may open at 18:30 UTC
    (90 min left) even though the 5-minute session-end buffer would allow it."""
    raw = {"id": "m1", "universe": {"float_max": 1e12, "price_min": 0.1},
           "triggers": ["hod_break"],
           "bracket": {"k_tp": 1.0, "k_sl": 2.0, "time_stop_minutes": 120},
           "sizing": {"risk_per_trade_pct_of_allocation": 0.5, "allocation_usd": 10_000.0,
                      "max_notional_usd": 20_000.0},
           "model": {"artifact_path": "x"}}
    cfg = ModelStrategyConfig.model_validate(raw)
    rt = DecisionRuntime(cfg, "AAAA", policy=_AlwaysTrade(), cost_source=CostModelAdapter(),
                         allow_unknown_float=True)
    t0 = datetime(2024, 3, 12, 14, 0, tzinfo=timezone.utc)

    def bar(ts):
        return Bar(symbol="AAAA", ts=ts, open=20.0, high=20.6, low=19.9, close=20.5,
                   volume=1e5, session_date=date(2024, 3, 12),
                   minute_of_day=ts.hour * 60 + ts.minute, segment=Segment.RTH)

    assert rt._within_session_cutoff(bar(t0 + timedelta(hours=4, minutes=30)))       # 18:30
    assert rt._within_session_cutoff(bar(t0 + timedelta(hours=4)))                   # 18:00 == H
    assert not rt._within_session_cutoff(bar(t0 + timedelta(hours=3, minutes=30)))  # 17:30


# ------------------------------------------------------------------ F3 degenerate folds

def test_degenerate_fold_is_skipped_and_reported(monkeypatch):
    from sgt.db.session import get_session
    from sgt.decision import train as train_mod
    from sgt.decision.train import TrainConfig

    real = train_mod._fit_model
    calls = {"n": 0}

    def flaky(*a, **kw):
        # the first OUTER fit (cfg_hash != "inner"/"null") fails like a single-class fold
        if a[5] not in ("inner", "null") and calls["n"] == 0:
            calls["n"] += 1
            raise ValueError("y contains a single class")
        return real(*a, **kw)

    monkeypatch.setattr(train_mod, "_fit_model", flaky)
    df = _df_full(seed=5)
    with get_session() as s:
        r = train_mod.train_decision_model(df, TrainConfig(run_id="degenerate", z_grid=(1.0,),
                                                                  p_min_grid=(0.5,)), session=s)
    assert calls["n"] == 1
    assert r.coverage["n_skipped_folds"] >= 1
    assert any("single class" in f["reason"] for f in r.coverage["skipped_folds"])


def test_degenerate_labels_ignores_nan():
    from sgt.decision.train import _degenerate_labels

    df = pd.DataFrame({"y": [0.01, np.nan, 0.02, np.nan]})
    assert _degenerate_labels(df, "y")            # finite labels all one sign
    df2 = pd.DataFrame({"y": [0.01, np.nan, -0.02]})
    assert not _degenerate_labels(df2, "y")


def _df_full(seed=0, n_sessions=50, n_symbols=5, per=8):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_sessions):
        sess = f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}"
        for s in (f"S{i}" for i in range(n_symbols)):
            for _ in range(per):
                f0 = float(rng.normal())
                rows.append({"symbol": s, "ts": f"{sess}T10:00", "session_date": sess,
                             "trigger": "ema_cross", "cost_rt_measured": 0.004,
                             "discovered_session": "2026-01-01", "f_sig": f0,
                             "f_n0": float(rng.normal()),
                             LABEL: 0.02 * f0 - 0.004 + float(rng.normal(scale=0.01)),
                             "uniq_w__tp2.0_sl1.0_h12": 1.0})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ F4 trial idempotency

def test_concurrent_duplicate_trial_is_idempotent():
    """Simulate the lost race: our SELECT saw nothing, another writer inserted first."""
    from sgt.db.session import get_session
    from sgt.decision import trial_ledger

    with get_session() as s:
        assert trial_ledger.record_trial(s, run_id="race", params={"a": 1}, metrics={})

    class _RacedSession:
        """Delegates to a real session but hides the existing row from the SELECT."""

        def __init__(self, real):
            self._real = real

        def execute(self, *_a, **_k):
            class _Empty:
                @staticmethod
                def first():
                    return None
            return _Empty()

        def __getattr__(self, name):
            return getattr(self._real, name)

    with get_session() as s:
        assert trial_ledger.record_trial(_RacedSession(s), run_id="race",
                                         params={"a": 1}, metrics={}) is False
        assert trial_ledger.trial_count(s, "race") == 1      # session still usable


# ------------------------------------------------------------------ F5 WAL readback

def test_wal_readback_warns_when_mode_not_switched(caplog):
    from sgt.db.session import _sqlite_pragmas

    class _Cur:
        def execute(self, sql):
            self.sql = sql
            return self

        def fetchone(self):
            return ("delete",) if "journal_mode" in self.sql else None

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cur()

    with caplog.at_level(logging.WARNING, logger="sgt.db.session"):
        _sqlite_pragmas(_Conn(), None)
    assert any("WAL not enabled" in r.message and "delete" in r.message
               for r in caplog.records)


def test_wal_readback_silent_when_wal(caplog):
    from sgt.db.session import _sqlite_pragmas

    class _Cur:
        def execute(self, sql):
            return self

        def fetchone(self):
            return ("wal",)

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cur()

    with caplog.at_level(logging.WARNING, logger="sgt.db.session"):
        _sqlite_pragmas(_Conn(), None)
    assert not caplog.records
