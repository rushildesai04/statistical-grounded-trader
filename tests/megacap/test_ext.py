"""Extended daily-model forward set (prereg_megacap_ext_forward_2026-11-09): set selection, its separate
decisions/marks/models directory, and the ``--set ext`` config. Reuses the same model code as the six-name
test; only the ticker list and the reports root differ."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sgt.megacap import live
from sgt.megacap.daily import DAILY_FEATURES

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import megacap_forward as mf  # noqa: E402

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[2]


def _features(n=400):
    rng = np.random.default_rng(1)
    sess = [d.date() for d in pd.bdate_range("2025-01-02", periods=n)]
    df = pd.DataFrame({f: rng.normal(size=n) for f in DAILY_FEATURES})
    df["session"], df["label"] = sess, rng.normal(0, 0.01, n)
    return df


def test_ext_tickers_are_exactly_the_tape_minus_the_six():
    tape = json.loads((REPO / "config/models/model_tape.json").read_text())
    tape_syms = [r["symbol"] for r in tape]
    expected = [s for s in tape_syms if s not in live.TICKERS]
    assert list(live.EXT_TICKERS) == expected                      # frozen set == tape order minus the six
    assert len(live.EXT_TICKERS) == 15
    assert not set(live.EXT_TICKERS) & set(live.TICKERS)           # disjoint from the six-name test
    assert set(live.EXT_TICKERS) | set(live.TICKERS) == set(tape_syms)


def test_set_registry_is_wired_and_prereg_committed():
    orig, ext = mf.SETS["orig"], mf.SETS["ext"]
    assert orig.tickers == live.TICKERS and orig.root == live.ROOT
    assert ext.tickers == live.EXT_TICKERS and ext.root == live.ROOT_EXT
    assert ext.root != orig.root and ext.run_id != orig.run_id      # own directory and trial-ledger run
    assert ext.first_decision == date(2026, 11, 9)
    assert ext.symbols == list(live.EXT_TICKERS) + ["SPY", "QQQ"]
    assert (REPO / ext.prereg).exists()                            # committed before any decision


def test_decide_and_mark_use_the_ext_root_and_tickers(tmp_path, monkeypatch):
    live.train_month("2026-11", {s: _features() for s in live.EXT_TICKERS}, root=tmp_path)
    assert (tmp_path / "models" / "2026-11" / "meta.json").exists()
    monkeypatch.setattr(live, "today_row", lambda *a, **k: _features(1).iloc[0])
    p = live.decide(date(2026, 11, 9), datetime(2026, 11, 9, 15, 50, tzinfo=ET), {}, {},
                    tickers=live.EXT_TICKERS, root=tmp_path)
    assert p == tmp_path / "decisions" / "2026-11-09.json"
    dec = json.loads(p.read_text())
    assert set(dec["positions"]) == set(live.EXT_TICKERS)          # ext names only, none of the six
    assert all(v["position"] in (-1, 0, 1) for v in dec["positions"].values())

    idx = [date(2026, 11, 9), date(2026, 11, 10)]
    daily = {s: pd.DataFrame({"close": [100.0, 101.0]}, index=idx) for s in live.EXT_TICKERS}
    m = live.mark(daily, root=tmp_path)
    assert set(m.symbol) == set(live.EXT_TICKERS) and not m.void.any()


def test_default_root_still_the_six_name_forward(tmp_path, monkeypatch):
    # The original path is untouched: no root/tickers args => module ROOT/TICKERS (six-name test).
    monkeypatch.setattr(live, "ROOT", tmp_path)
    live.train_month("2026-11", {s: _features() for s in live.TICKERS})
    monkeypatch.setattr(live, "today_row", lambda *a, **k: _features(1).iloc[0])
    p = live.decide(date(2026, 11, 9), datetime(2026, 11, 9, 15, 50, tzinfo=ET), {}, {})
    assert p == tmp_path / "decisions" / "2026-11-09.json"
    assert set(json.loads(p.read_text())["positions"]) == set(live.TICKERS)


def test_set_flag_selects_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                     # cfg.root.mkdir lands under tmp, not the repo
    captured = {}

    def fake_auto(cfg):
        captured["cfg"] = cfg
        return 0
    monkeypatch.setattr(mf, "auto", fake_auto)
    monkeypatch.setattr(sys, "argv", ["megacap_forward.py", "auto", "--set", "ext"])
    assert mf.main() == 0
    assert captured["cfg"] is mf.SETS["ext"]


def test_retrain_refuses_a_thin_history():
    import megacap_forward as mf

    class Store:
        def __init__(self, days):
            self.days = days

        def sessions(self, s):
            return self.days
    cfg = mf.SETS["ext"]
    upto = date(2026, 10, 30)
    recent = [upto - timedelta(days=i) for i in range(45)]
    with pytest.raises(mf.LiveError, match="backfill from"):
        mf.check_history(Store(recent), cfg, upto)
    cal_days = [mf.HIST_START + timedelta(days=i) for i in range((upto - mf.HIST_START).days + 1)]
    full = [d for d in cal_days if mf.default_calendar().is_session(d)]
    mf.check_history(Store(full), cfg, upto)                                   # complete: passes
    with pytest.raises(mf.LiveError, match="sessions stored"):
        mf.check_history(Store(full[::2]), cfg, upto)                          # half the sessions missing
