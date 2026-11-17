"""The replay lab shows what the rule modules decide, no earlier than they could decide it (synthetic bars only)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sgt.fast import exits
from sgt.lab import page, replay

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import lab  # noqa: E402

DAY = "2026-11-17"
OPEN = pd.Timestamp("2026-11-17T14:30:00Z")                   # 09:30 ET


def bars(closes, *, opens=None, highs=None, lows=None, vol=None, skip=()):
    n = len(closes)
    opens = list(opens) if opens is not None else [closes[0]] + list(closes[:-1])
    df = pd.DataFrame({"ts": [OPEN + pd.Timedelta(minutes=i) for i in range(n)], "open": opens, "close": closes,
                       "high": highs if highs is not None else [max(o, c) for o, c in zip(opens, closes)],
                       "low": lows if lows is not None else [min(o, c) for o, c in zip(opens, closes)],
                       "volume": vol if vol is not None else [1000.0] * n})
    return df.drop(index=list(skip)).reset_index(drop=True)


def walk(seed=3, n=390):
    """A jumpy small-cap session: random walk with bursts, so flags, pivots and breaks all occur."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.006, n)
    for a in (40, 120, 200, 300):
        r[a:a + 6] += 0.02
        r[a + 6:a + 10] -= 0.008
    c = 5.0 * np.exp(np.cumsum(r))
    o = np.concatenate([[5.0], c[:-1]])
    wig = np.abs(rng.normal(0, 0.003, n))
    v = rng.uniform(800, 1500, n)
    for a in (40, 120, 200, 300):
        v[a:a + 6] *= 6
    return bars(list(c), opens=list(o), highs=list(np.maximum(o, c) * (1 + wig)), lows=list(np.minimum(o, c) * (1 - wig)),
                vol=list(v))


def at(minute, second=0):
    return (OPEN + pd.Timedelta(minutes=minute, seconds=second)).isoformat()


def test_fade_trade_is_the_first_bar_after_the_alert_with_a_20_percent_stop():
    c = [10.0] * 10 + [10.5, 11.0, 11.6, 12.1] + [11.0] * 80
    r = replay.replay(bars(c), "TEST", DAY, [{"at": at(10, 30), "type": "volume_spike"}])
    (short,) = [t for t in r["trades"] if t["rule"] == "fade"]
    assert short["entry_hm"] == "09:41" and short["entry"] == pytest.approx(10.5)      # bar 11 opens at bar 10's close
    assert short["stop"] == pytest.approx(10.5 * 1.2) and short["book"] is True
    assert short["reason"] == "60 minutes" and short["exit_hm"] == "10:40"              # 60 bars: 09:41 .. 10:40
    assert short["gross"] == pytest.approx((10.5 - 11.0) / 10.5)
    assert short["net"] == pytest.approx(short["gross"] - short["cost"]) and short["cost"] > 0.005   # spread + locate
    assert r["alerts"][0]["traded"] is True and r["summary"]["trades"] == len(r["trades"])
    json.dumps(r, allow_nan=False)                                                      # plain numbers only


def test_a_stop_is_shown_at_the_bar_that_crossed_it_even_across_missing_minutes():
    c = [10.0] * 12 + [10.2] * 20 + [12.6] + [12.0] * 30
    hi = [10.0] * 12 + [10.2] * 20 + [12.6] + [12.0] * 30
    f = bars(c, highs=hi, skip=range(15, 25))                    # ten minutes without a print inside the hold
    r = replay.replay(f, "TEST", DAY, [{"at": at(11, 0), "type": "new_high"}])
    (short,) = [t for t in r["trades"] if t["rule"] == "fade"]
    assert short["entry_hm"] == "09:42" and short["reason"] == "stop"
    assert short["exit_hm"] == "10:02"                           # the 12.6 bar (minute 32), not entry + bars held
    assert short["gross"] == pytest.approx(-0.20, abs=1e-9) or short["gross"] < -0.20   # gap over the stop fills at the open
    assert any(h["minutes"] == 10 for h in r["halts"])


def test_only_the_first_tradable_alert_starts_trades():
    c = [10.0] * 60
    r = replay.replay(bars(c), "TEST", DAY, [{"at": at(5), "type": "halted"}, {"at": at(8), "type": "new_high"},
                                             {"at": at(20), "type": "volume_spike"}])
    assert [a["traded"] for a in r["alerts"]] == [False, True, False]
    assert "not one the rule trades" in r["alerts"][0]["why"] and "first one only" in r["alerts"][2]["why"]
    assert sum(t["rule"] == "fade" for t in r["trades"]) == 1


def test_an_alert_into_a_halt_is_not_shorted_and_says_why():
    c = [10.0] * 40
    f = bars(c, skip=range(11, 25))                               # nothing prints for 14 minutes after the alert
    r = replay.replay(f, "TEST", DAY, [{"at": at(10, 30), "type": "volume_spike"}])
    assert not [t for t in r["trades"] if t["rule"] == "fade"]
    assert any("no bar within 5 minutes" in n for n in r["notes"])


def test_price_and_hours_rules_are_applied_and_explained():
    r = replay.replay(bars([0.5] * 40), "TEST", DAY, [{"at": at(5), "type": "new_high"}])
    assert any("outside $1 to $50" in n for n in r["notes"]) and not r["trades"]
    late = bars([10.0] * 390)
    r = replay.replay(late, "TEST", DAY, [{"at": at(380), "type": "new_high"}])        # 15:50 ET: after the last entry
    assert any("outside the rule's hours" in n for n in r["notes"])


def test_no_alert_uses_our_own_spike_or_says_nothing_would_trade():
    r = replay.replay(bars([10.0] * 60), "TEST", DAY)
    assert not r["trades"] and any("nothing would have traded" in n for n in r["notes"])
    c = [10.0] * 30 + [10.2, 10.4, 10.6, 10.8, 11.0, 11.3] + [11.0] * 80
    v = [1000.0] * 30 + [9000.0] * 6 + [1000.0] * 80
    r = replay.replay(bars(c, vol=v), "TEST", DAY)
    assert r["spikes"] and r["spikes"][0]["kind"] == "spike"
    scan = [t for t in r["trades"] if t["rule"] == "scan"]
    assert len(scan) == 1 and scan[0]["side"] == "short" and scan[0]["book"] is False
    assert scan[0]["entry_time"] > r["spikes"][0]["time"]                               # read at the close, act next bar


def test_the_lab_never_shows_a_detection_before_it_could_be_made():
    f = walk()
    alerts = [{"at": at(46, 20), "type": "volume_spike"}]
    full = replay.replay(f, "TEST", DAY, alerts)
    assert len(full["flags"]) >= 1 and len(full["structure"]["breaks"]) >= 2 and len(full["trades"]) >= 2   # not vacuous
    for k in (60, 110, 180, 260, 330):
        part = replay.replay(f.iloc[:k], "TEST", DAY, alerts)
        last = part["candles"][-1]["time"]
        for key, items, full_items in (("flag", part["flags"], full["flags"]),
                                       ("break", part["structure"]["breaks"], full["structure"]["breaks"])):
            assert items == [x for x in full_items if x["time"] <= last], (key, k)
        known = [p for p in full["structure"]["pivots"] if p["known"] <= last]
        assert part["structure"]["pivots"] == known, k
        done = [t for t in full["trades"] if t["exit_time"] < last]                    # finished before the cut
        for t in done:
            assert t in part["trades"], (k, t["rule"])
        for t in part["trades"]:                                                        # an entry seen early is real
            assert any(u["rule"] == t["rule"] and u["entry_time"] == t["entry_time"] and u["entry"] == t["entry"]
                       for u in full["trades"]), (k, t["rule"])


def test_long_setups_come_from_the_books_own_code_and_one_is_marked_as_the_books_trade():
    f = walk()
    r = replay.replay(f, "TEST", DAY, [{"at": at(46, 20), "type": "volume_spike"}])
    longs = [t for t in r["trades"] if t["side"] == "long"]
    assert longs and sum(t["book"] for t in longs) == 1
    first = min((t for t in longs if not t["nowin"]), key=lambda t: t["entry_time"])
    assert first["book"] is True                             # the first long whose target can beat costs
    for t in longs:
        assert t["nowin"] == ((t["target"] - t["entry"]) / t["entry"] <= t["cost"])
        assert ("the book skips it" in t["name"]) == t["nowin"]
    for t in longs:
        assert t["rule"] in replay.RULES and t["stop"] < t["entry"] < t["target"]
        assert t["target"] == pytest.approx(t["entry"] + 2 * (t["entry"] - t["stop"]))
    lines = [e["text"] for e in r["timeline"]]
    assert any(x.startswith("alert 10:16") for x in lines) and [e["time"] for e in r["timeline"]] == sorted(
        e["time"] for e in r["timeline"])


def test_bad_bars_are_refused():
    with pytest.raises(ValueError):
        replay.replay(pd.DataFrame({"ts": [OPEN], "open": [1.0]}), "TEST", DAY)
    with pytest.raises(ValueError):
        replay.replay(bars([10.0] * 501), "TEST", DAY)
    with pytest.raises(ValueError):
        replay.replay(bars([10.0, -1.0, 10.0]), "TEST", DAY)
    empty = replay.replay(pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"]), "TEST", DAY)
    assert empty["bars"] == 0 and empty["notes"] == ["no bars for this session"]


def test_the_fade_in_the_lab_is_the_champion_exit():
    c = [10.0] * 10 + [9.0] * 100
    r = replay.replay(bars(c), "TEST", DAY, [{"at": at(3), "type": "new_high"}])
    (short,) = [t for t in r["trades"] if t["rule"] == "fade"]
    f = replay._clean(bars(c))
    ref = exits.simulate(exits.indicators(f), f.ts[4], 10.0, exits.CHAMPION)
    assert short["gross"] == pytest.approx(ref["gross"]) and short["minutes"] == ref["minutes"] - 1


def test_page_embeds_untrusted_text_safely_and_is_bounded():
    r = replay.replay(bars([10.0] * 60), "TEST", DAY, [{"at": at(5), "type": "</script><img src=x onerror=1>"}])
    html = page.render([r], datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc))
    assert "</script><img" not in html and "\\u003c/script>\\u003cimg" in html
    assert html.count("/*LAB*/null") == 0 and "const DATA = {" in html and html.startswith("<!doctype html>")
    assert "innerHTML" not in html                               # the page writes text with textContent only
    many = page.render([r] * (page.MAX_REPLAYS + 20), datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc))
    assert json.loads(many.split("const DATA = ", 1)[1].split(";\nconst css", 1)[0])["replays"].__len__() == page.MAX_REPLAYS


def test_cli_validates_requests_and_converts_eastern_alert_times(tmp_path):
    assert lab.alert_at(DAY, "10:02:02") == "2026-11-17T15:02:02+00:00"
    assert lab.alert_at("2026-07-01", "10:02") == "2026-07-01T14:02:00+00:00"          # summer time
    s = lab.spec("mpu", DAY, [{"at": "2026-11-17 15:02:02", "type": "x" * 100}], live="y" * 999)
    assert s["symbol"] == "MPU" and s["alerts"][0]["at"].endswith("+00:00") and len(s["alerts"][0]["type"]) == 40
    assert len(s["live"]) == 300
    for bad in ("../etc", "MPU;rm", "", "TOOLONGTICKER"):
        with pytest.raises(ValueError):
            lab.spec(bad, DAY)
    with pytest.raises(ValueError):
        lab.spec("MPU", "17-11-2026")
    with pytest.raises(ValueError):
        lab.alert_at(DAY, "10")
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"symbol": "MPU"}))
    with pytest.raises(ValueError):
        lab.from_file(p)
    p.write_text(json.dumps([{"symbol": "MPU", "session": DAY}] * 200))
    assert len(lab.from_file(p)) == lab.MAX_EVENTS


def test_cli_random_is_repeatable_and_build_skips_what_has_no_bars(tmp_path, capsys):
    csv = tmp_path / "t.csv"
    pd.DataFrame({"symbol": [f"AB{i}" for i in range(30)], "alert_type": ["new_high"] * 30,
                  "posted_at": ["2026-01-05 15:00:00+00:00"] * 30, "session": ["2026-01-05"] * 30,
                  "status": ["ok"] * 25 + ["no_entry"] * 5}).to_csv(csv, index=False)
    a, b = lab.random_specs(5, 7, csv), lab.random_specs(5, 7, csv)
    assert a == b and len(a) == 5 and all(x["symbol"] in {f"AB{i}" for i in range(25)} for x in a)
    assert lab.random_specs(5, 8, csv) != a
    one = bars([10.0] * 30)
    got = lab.build([lab.spec("AAA", DAY), lab.spec("BBB", DAY)], lambda s, d: one if s == "AAA" else pd.DataFrame(),
                    datetime.now(timezone.utc))
    assert [r["symbol"] for r in got] == ["AAA"] and "BBB 2026-11-17: no bars" in capsys.readouterr().out
