from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fade_challengers as fc  # noqa: E402
from alert_fade_test import score as research_score  # noqa: E402
from fast.test_fade import write_bars  # noqa: E402
from sgt.intraday.store import ParquetBarStore  # noqa: E402

ENTRY_TS = "2026-11-02 15:01:00+00:00"                     # 10:01 ET = RTH minute 31


def ev(sym: str) -> pd.DataFrame:
    return pd.DataFrame([{"symbol": sym, "session": "2026-11-02", "alert_type": "volume_spike", "entry_ts": ENTRY_TS,
                          "entry": 10.0, "cost_rt": 0.01}])


def test_champion_equals_research_score_and_variants_move_entry_and_window(tmp_path):
    store = ParquetBarStore(tmp_path)
    write_bars(store, "DRP", {m: (9.0, 9.0) for m in range(34, 70)})       # 9.00 from 10:04 to 10:39, then 10.00
    write_bars(store, "SPK", {50: (10.0, 13.0)})                           # 10:20 bar hits the 20% stop
    for sym in ("DRP", "SPK"):
        t = fc.score(ev(sym), store).iloc[0]
        assert t.net_champion == pytest.approx(research_score(ev(sym), store).net.iloc[0], abs=1e-12)
    drp = fc.score(ev("DRP"), store).iloc[0]
    assert drp.net_champion == pytest.approx(0.0 - 0.01 - fc.LOCATE)     # back to 10.00 at the 60-min close
    assert drp.net_D4 == pytest.approx((9.0 - 10.0) / 9.0 - 0.01 - fc.LOCATE)   # enters at 9.00 (10:05), covers 10.00
    assert drp.net_H30 == pytest.approx(0.10 - 0.01 - fc.LOCATE)         # still 9.00 at 10:30
    spk = fc.score(ev("SPK"), store).iloc[0]
    assert spk.net_H30 == pytest.approx(-0.20 - 0.01 - fc.LOCATE)        # stop inside 30 min too


def test_no_rth_bar_is_no_trade_and_late_delay_runs_out_of_session(tmp_path):
    store = ParquetBarStore(tmp_path)
    assert np.isnan(fc.score(ev("NONE"), store).iloc[0].net_D2)
    write_bars(store, "EOD", {})
    late = ev("EOD").assign(entry_ts="2026-11-02 20:57:00+00:00")        # 15:57 ET: +4 min is past the last bar
    assert np.isnan(fc.score(late, store).iloc[0].net_D4) and not np.isnan(fc.score(late, store).iloc[0].net_D2)


def test_gate_is_paired_by_session_with_bonferroni_and_tail_guard():
    rng = np.random.default_rng(0)
    n = 400
    base = rng.normal(0.01, 0.1, n)
    t = pd.DataFrame({"session": [f"d{i // 2}" for i in range(n)], "net_champion": base,
                      "net_D2": base + 0.02, "net_D4": base - 0.03, "net_H30": base,
                      "net_H90": np.where(np.arange(n) % 50 == 0, -0.9, base + 0.03)})
    res = fc.evaluate(t)
    assert res["D2"]["pass"] and not res["H30"]["pass"]
    assert not res["H90"]["pass"]                      # better mean but a far worse tail: fails the tail guard
    assert not res["D4"]["pass"] and res["d4_costs_money"]
    assert fc.ALPHA == pytest.approx(0.0125)


def test_shadow_summary_reaches_the_digest_and_bad_files_are_ignored(tmp_path):
    import json

    from sgt.ops import digest

    t = pd.DataFrame({f"net_{v.id}": [0.02, 0.0] for v in (fc.CHAMPION, *fc.CHALLENGERS)})
    line = fc.summary_line(t)
    assert line.startswith("fade challengers fwd n=2: champion +1.00%, D2 +1.00%")
    assert digest.challenger_research(tmp_path) is None
    (tmp_path / "shadow_summary.json").write_text(json.dumps({"line": line}))
    assert digest.challenger_research(tmp_path) == line
    (tmp_path / "shadow_summary.json").write_text("{not json")
    assert digest.challenger_research(tmp_path) is None
