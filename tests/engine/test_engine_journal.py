"""JournalWriter persists trades and candidates (taken and skipped) to the DB."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from sgt.db.session import get_session
from sgt.engine.backtest import default_risk_config, run_backtest
from sgt.engine.journal import JournalWriter
from sgt.engine.models import CandidateJournal, TradeJournal
from sgt.intraday.calendar import default_calendar
from sgt.intraday.synthetic import Ignition, make_multi_session
from sgt.core.types import Segment
from sgt.strategy.loader import config_hash, load_strategy
from sgt.strategy.runtime import EntryOverride


def _run():
    cfg = load_strategy("tests/fixtures/trader_v0_mechanics.yaml")
    sess = default_calendar().previous_sessions(date(2024, 3, 15), 3)
    bars = make_multi_session("AAAA", sess, seed=1, start_price=6.0,
                              segments=(Segment.PRE, Segment.RTH, Segment.POST),
                              ignitions=(Ignition(start_minute=70, length=15,
                                                  drift_per_bar=0.02, volume_mult=12.0),))
    res = run_backtest(cfg, {"AAAA": bars}, starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), run_id="jrun", allow_unknown_float=True)
    return cfg, res


def test_journal_writer_persists_trades_and_candidates():
    cfg, res = _run()
    jw = JournalWriter(run_id=res.run_id, mode="backtest", config_hash=config_hash(cfg))
    n_tr = jw.write_trades(res.trades)
    n_ca = jw.write_candidates(res.candidates)
    assert n_tr == len(res.trades) and n_ca == len(res.candidates)

    with get_session() as s:
        tr_count = s.scalar(select(func.count()).select_from(TradeJournal)
                            .where(TradeJournal.run_id == "jrun"))
        ca_count = s.scalar(select(func.count()).select_from(CandidateJournal)
                            .where(CandidateJournal.run_id == "jrun"))
        assert tr_count == len(res.trades)
        assert ca_count == len(res.candidates)
        # a persisted trade carries an exit reason and a run/config scope
        row = s.scalars(select(TradeJournal).where(TradeJournal.run_id == "jrun")).first()
        assert row.mode == "backtest" and row.config_hash == config_hash(cfg)
        assert row.exit_reason != ""


def test_journal_records_skipped_candidates_with_reasons():
    cfg = load_strategy("tests/fixtures/trader_v0_mechanics.yaml")
    rc = default_risk_config(cfg)
    rc = rc.model_copy(update={"limits": rc.limits.model_copy(update={"min_price": 1.0e6})})
    sess = default_calendar().previous_sessions(date(2024, 3, 15), 2)
    bars = make_multi_session("AAAA", sess, seed=2, start_price=6.0,
                              segments=(Segment.RTH,),
                              ignitions=(Ignition(start_minute=70, length=15,
                                                  drift_per_bar=0.02, volume_mult=12.0),))
    res = run_backtest(cfg, {"AAAA": bars}, starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), risk_config=rc, run_id="skiprun", allow_unknown_float=True)
    jw = JournalWriter(run_id="skiprun", mode="backtest", config_hash=config_hash(cfg))
    jw.write_candidates(res.candidates)
    with get_session() as s:
        risk_skips = s.scalar(select(func.count()).select_from(CandidateJournal)
                              .where(CandidateJournal.run_id == "skiprun",
                                     CandidateJournal.skip_reason == "risk"))
        assert risk_skips >= 1
