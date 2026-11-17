"""Plumbing of prereg_fade_entry_seconds_2026-11-17 on synthetic prints and bars (the real run is made once)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_entry_seconds as st  # noqa: E402

DAY = "2026-03-03"
ALERT = pd.Timestamp("2026-03-03T15:02:20Z")                      # 10:02:20 ET; the bar rule enters at 10:03:00


def tr(*rows):
    """Prints as (HH:MM:SS UTC, price)."""
    return np.array([[pd.Timestamp(f"2026-03-03T{t}Z").value, px, 100.0] for t, px in rows])


def bars(start="15:00", closes=None, highs=None):
    closes = closes or [10.0] * 90
    t0 = pd.Timestamp(f"2026-03-03T{start}:00Z")
    opens = [closes[0]] + list(closes[:-1])
    return pd.DataFrame({"ts": [t0 + pd.Timedelta(minutes=i) for i in range(len(closes))], "open": opens,
                         "close": closes, "high": highs or [max(o, c) for o, c in zip(opens, closes)],
                         "low": [min(o, c) for o, c in zip(opens, closes)], "volume": [1000.0] * len(closes)})


def test_minutes_needed_cover_the_latest_entry_and_its_tolerance():
    assert [m.strftime("%H:%M") for m in st.minutes_needed(ALERT)] == ["15:02", "15:03"]
    late = pd.Timestamp("2026-03-03T15:02:55Z")                  # + 60 s + 10 s tolerance reaches 15:04:05
    assert [m.strftime("%H:%M") for m in st.minutes_needed(late)] == ["15:02", "15:03", "15:04"]


def test_first_print_needs_a_print_within_ten_seconds():
    t = tr(("15:02:25", 10.0), ("15:02:31", 10.2), ("15:02:58", 10.5))
    assert st.first_print(t, ALERT + pd.Timedelta(seconds=10)) == (1, 10.2)       # 15:02:30 -> the 15:02:31 print
    assert st.first_print(t, ALERT + pd.Timedelta(seconds=15)) is None             # next print is 23 s later
    assert st.first_print(t, ALERT + pd.Timedelta(seconds=30)) == (2, 10.5)
    assert st.first_print(np.empty((0, 3)), ALERT) is None
    assert st.first_print(t, ALERT + pd.Timedelta(seconds=60)) is None             # nothing after 15:03:20


def test_the_entry_minute_is_rebuilt_from_prints_after_the_entry_only():
    t = tr(("15:02:05", 14.0), ("15:02:31", 10.0), ("15:02:40", 10.4), ("15:02:59", 9.8), ("15:03:01", 9.0))
    b = st.entry_bar(t, 1)
    assert (b["open"], b["high"], b["low"], b["close"]) == (10.0, 10.4, 9.8, 9.8)   # the 14.0 print came before the entry
    assert b["ts"] == pd.Timestamp("2026-03-03T15:02:00Z") and b["volume"] == 300.0


def test_a_spike_before_the_entry_cannot_stop_the_trade_and_one_after_does():
    f = bars(highs=[10.0, 10.0, 14.0] + [10.0] * 87)             # the 15:02 bar's high is 14.0 (40% up)
    before = tr(("15:02:05", 14.0), ("15:02:31", 10.0), ("15:02:50", 10.1))
    v = st.variant(f, before, ALERT, 10, DAY)
    assert v["how"] == "cap" and v["entry"] == 10.0 and v["gross"] == pytest.approx(0.0)
    after = tr(("15:02:31", 10.0), ("15:02:50", 12.5))            # 25% above the entry, after it
    v = st.variant(f, after, ALERT, 10, DAY)
    assert v["how"] == "stop" and v["gross"] == pytest.approx(-0.20)


def test_variant_respects_price_hours_and_missing_prints():
    f = bars()
    assert st.variant(f, tr(("15:02:31", 0.5)), ALERT, 10, DAY) is None             # under $1
    assert st.variant(f, tr(("15:02:25", 10.0)), ALERT, 10, DAY) is None             # no print at or after + 10 s
    late = pd.Timestamp("2026-03-03T20:50:20Z")                                     # 15:50 ET: after the last entry
    assert st.variant(bars("20:40"), tr(("20:50:31", 10.0)), late, 10, DAY) is None
    v = st.variant(bars(closes=[10.0] * 3 + [9.0] * 87), tr(("15:02:31", 10.0)), ALERT, 10, DAY)
    assert v["how"] == "cap" and v["gross"] == pytest.approx(0.10) and str(v["at"])[11:19] == "15:02:31"


def frame(n=300, edge=0.0, seed=0):
    rng = np.random.default_rng(seed)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-02", periods=n // 3)]
    champ = rng.normal(0.01, 0.10, n)
    t = pd.DataFrame({"symbol": [f"S{i}" for i in range(n)], "session": [days[i % len(days)] for i in range(n)],
                      "kind": "ok", "second": rng.integers(0, 60, n), "cost": 0.009, "bar_entry": 10.0,
                      "net_champion": champ, "how_champion": "cap"})
    for s in st.SECONDS:
        t[f"net_S{s}"] = champ + edge + rng.normal(0, 0.01, n)
        t[f"how_S{s}"] = "cap"
        t[f"px_S{s}"] = 10.0 * (1 + edge)
    return t


def test_gate_passes_a_real_gain_and_fails_noise_or_a_loss(monkeypatch):
    monkeypatch.setattr(st, "N_BOOT", 600)
    good = st.evaluate(frame(edge=0.01))
    assert all(v["pass"] for v in good.values()) and good["S10"]["ok_events"]["entry_vs_bar_mean"] == pytest.approx(0.01)
    assert not any(v["pass"] for v in st.evaluate(frame(edge=0.0)).values())
    assert not any(v["pass"] for v in st.evaluate(frame(edge=-0.01)).values())


def test_a_leg_that_does_not_trade_counts_zero_and_halts_are_reported(monkeypatch):
    monkeypatch.setattr(st, "N_BOOT", 300)
    t = frame(n=60)
    halt = t.iloc[:10].assign(kind="halt", net_champion=np.nan, how_champion=None, bar_entry=np.nan)
    for s in st.SECONDS:
        halt[f"net_S{s}"] = -0.20                                  # an early entry trades the halts and loses on them
    t2 = pd.concat([t, halt], ignore_index=True)
    r = st.evaluate(t2)["S10"]
    assert r["halt_events"] == {"events": 10, "traded": 10, "mean": pytest.approx(-0.20), "total": pytest.approx(-2.0)}
    assert r["total"] == pytest.approx(t.net_S10.sum() - 2.0) and r["champ_total"] == pytest.approx(t.net_champion.sum())
    assert r["trades"] == 70 and r["champ_trades"] == 60 and r["pass"] is False
    t3 = t.copy()
    t3.loc[:29, "net_S10"] = np.nan                                # the variant misses half the trades: they count 0
    assert st.evaluate(t3)["S10"]["ok_events"]["variant_no_print"] == 30


def test_main_refuses_a_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result_train.json").write_text("{}")
    called = []
    monkeypatch.setattr(st, "all_events", lambda split="train": called.append(split))
    assert st.main([]) == 2 and called == []
