"""Scanner: point-in-time universe filtering and deterministic ranking (§12)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from sgt.core.interfaces import ReferenceData
from sgt.core.types import Bar, Segment
from sgt.scanner import Scanner, UniverseFilter

UTC = timezone.utc
PRIOR = date(2026, 10, 9)
CUR = date(2026, 10, 12)
_MIDNIGHT_CUR = datetime.combine(CUR, datetime.min.time(), tzinfo=UTC)

# t = close of the last current-session bar used below (minute 572 closes at 573).
T = _MIDNIGHT_CUR + timedelta(minutes=573)


def _bar(sym: str, d: date, minute: int, open_: float, close: float, vol: float,
         seg: Segment = Segment.RTH) -> Bar:
    ts = datetime.combine(d, datetime.min.time(), tzinfo=UTC) + timedelta(minutes=minute)
    hi = max(open_, close)
    lo = min(open_, close)
    return Bar(sym, ts, open_, hi, lo, close, vol, d, minute, seg)


def _history(sym: str, *, prior_close: float = 5.0, first_open: float = 5.5,
             cur_close: float = 5.5, prior_vol: float = 100.0, cur_vol: float = 300.0
             ) -> list[Bar]:
    """Prior session (baseline) + current session so rel-vol cum == cur/prior and
    gap% is computable. With the defaults: cum rel-vol = 3.0, gap% = 10.0,
    price = 5.5, cum$vol = 3*cur_vol*cur_close."""
    bars = [_bar(sym, PRIOR, m, prior_close, prior_close, prior_vol) for m in (570, 571, 572)]
    bars.append(_bar(sym, CUR, 570, first_open, cur_close, cur_vol))
    bars.append(_bar(sym, CUR, 571, cur_close, cur_close, cur_vol))
    bars.append(_bar(sym, CUR, 572, cur_close, cur_close, cur_vol))
    return bars


def _ref(free_float: float | None = 30_000_000.0, dilution: bool = False):
    def inner(symbol: str, as_of: date) -> ReferenceData:
        assert as_of == CUR
        return ReferenceData(symbol=symbol, as_of=as_of, free_float=free_float,
                             dilution_flag=dilution)
    return inner


def _scan(filt: UniverseFilter, candidates, *, ref=None, attention=None,
          alerts=frozenset(), max_watchlist=20):
    sc = Scanner(filt, max_watchlist=max_watchlist)
    return sc.scan(T, candidates, reference=ref or _ref(), attention=attention,
                   alert_symbols=alerts)


# --------------------------------------------------------------------------- feature sanity


def test_features_computed_point_in_time():
    out = _scan(UniverseFilter(), {"AAA": _history("AAA")})
    assert len(out) == 1
    f = out[0].features
    assert f["price"] == 5.5
    assert f["cum_rel_vol"] == pytest.approx(3.0)
    assert f["gap_pct"] == pytest.approx(10.0)
    assert f["dollar_volume"] == pytest.approx(3 * 300.0 * 5.5)
    assert f["free_float"] == 30_000_000.0


def test_bars_after_t_are_ignored():
    bars = _history("AAA")
    # A huge-volume bar at/after t must not change cum rel-vol or price.
    bars.append(_bar("AAA", CUR, 573, 9.0, 9.0, 1_000_000.0))  # ts == T -> excluded
    out = _scan(UniverseFilter(), {"AAA": bars})
    assert out[0].features["price"] == 5.5
    assert out[0].features["cum_rel_vol"] == pytest.approx(3.0)


# --------------------------------------------------------------------------- each criterion + boundary


@pytest.mark.parametrize("filt,passes", [
    (UniverseFilter(float_max=30_000_000), True),   # boundary inclusive
    (UniverseFilter(float_max=29_999_999), False),
    (UniverseFilter(price_min=5.5), True),          # boundary
    (UniverseFilter(price_min=5.51), False),
    (UniverseFilter(price_max=5.5), True),          # boundary
    (UniverseFilter(price_max=5.49), False),
    (UniverseFilter(min_rel_vol=3.0), True),        # boundary
    (UniverseFilter(min_rel_vol=3.01), False),
    (UniverseFilter(min_gap_pct=10.0), True),       # boundary
    (UniverseFilter(min_gap_pct=10.01), False),
    (UniverseFilter(min_dollar_volume=3 * 300.0 * 5.5), True),   # boundary
    (UniverseFilter(min_dollar_volume=3 * 300.0 * 5.5 + 1), False),
])
def test_each_criterion_boundary(filt, passes):
    out = _scan(filt, {"AAA": _history("AAA")})
    assert bool(out) is passes


def test_exclude_dilution():
    cand = {"AAA": _history("AAA")}
    assert _scan(UniverseFilter(exclude_dilution=True), cand, ref=_ref(dilution=True)) == []
    assert len(_scan(UniverseFilter(exclude_dilution=True), cand, ref=_ref(dilution=False))) == 1


def test_unknown_float_fails_closed_when_float_max_set():
    out = _scan(UniverseFilter(float_max=40_000_000), {"AAA": _history("AAA")},
                ref=_ref(free_float=None))
    assert out == []


# --------------------------------------------------------------------------- gap uses prior RTH close


def test_gap_uses_prior_session_last_rth_close():
    # Prior close 4.0, first open 4.4 -> gap 10%.
    bars = _history("AAA", prior_close=4.0, first_open=4.4, cur_close=4.4)
    out = _scan(UniverseFilter(), {"AAA": bars})
    assert out[0].features["gap_pct"] == pytest.approx(10.0)


def test_gap_none_when_no_prior_session():
    bars = [_bar("AAA", CUR, m, 5.5, 5.5, 300.0) for m in (570, 571, 572)]
    out = _scan(UniverseFilter(min_gap_pct=1.0), {"AAA": bars})
    assert out == []  # gap unknown + criterion set -> fail closed


# --------------------------------------------------------------------------- ranking / ties / alerts


def test_alerted_symbol_scores_higher_but_must_pass_filters():
    cand = {"AAA": _history("AAA"), "BBB": _history("BBB")}
    out = _scan(UniverseFilter(), cand, alerts={"AAA"})
    assert [w.symbol for w in out] == ["AAA", "BBB"]
    assert out[0].score > out[1].score
    assert "alerted" in out[0].reasons


def test_alerted_but_failing_symbol_excluded():
    # AAA is alerted but priced out by price_max; it must not appear.
    cand = {"AAA": _history("AAA", first_open=5.5, cur_close=9.0, prior_close=8.18),
            "BBB": _history("BBB")}
    out = _scan(UniverseFilter(price_max=6.0), cand, alerts={"AAA"})
    assert [w.symbol for w in out] == ["BBB"]


def test_ties_break_by_symbol():
    cand = {"CCC": _history("CCC"), "AAA": _history("AAA"), "BBB": _history("BBB")}
    out = _scan(UniverseFilter(), cand)
    assert [w.symbol for w in out] == ["AAA", "BBB", "CCC"]


def test_max_watchlist_size_enforced():
    cand = {f"S{i:02d}": _history(f"S{i:02d}") for i in range(25)}
    out = _scan(UniverseFilter(), cand, max_watchlist=20)
    assert len(out) == 20


def test_attention_z_feeds_score():
    def att(sym, t):
        return {"mention_velocity_z": 5.0 if sym == "AAA" else 0.0}
    cand = {"AAA": _history("AAA"), "BBB": _history("BBB")}
    out = _scan(UniverseFilter(), cand, attention=att)
    assert out[0].symbol == "AAA"
    assert out[0].features["attention_z"] == 5.0


def test_price_band_validation():
    with pytest.raises(ValueError):
        UniverseFilter(price_min=6.0, price_max=5.0)
