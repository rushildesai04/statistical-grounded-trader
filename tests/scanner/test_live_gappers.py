"""Live gap scanner: same rule as the datasets; cache per session; fail closed."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sgt.scanner.live_gappers import LiveGapScanner, select_gappers

TODAY = date(2026, 10, 13)


def _snap(o, pc, pv, d="2026-10-13"):
    return {"dailyBar": {"t": f"{d}T08:00:00Z", "o": o}, "prevDailyBar": {"c": pc, "v": pv}}


def test_select_gappers_rule():
    snaps = {"AAA": _snap(5.6, 5.0, 1e6), "BBB": _snap(5.4, 5.0, 1e6),    # 12% vs 8%
             "CCC": _snap(40, 30, 1e6), "DDD": _snap(5.6, 5.0, 1e3),     # >$30, thin
             "EEE": _snap(5.6, 5.0, 1e6, d="2026-10-12"), "FFF": {"bad": 1}}
    assert select_gappers(snaps, today=TODAY) == {"AAA"}


def test_cache_is_per_session_and_fail_closed():
    now = {"t": datetime(2026, 10, 13, 13, 35, tzinfo=timezone.utc)}
    sc = LiveGapScanner(["aaa", "bbb"], clock=lambda: now["t"],
                        fetch=lambda syms: {"AAA": _snap(5.6, 5.0, 1e6)})
    assert sc.symbols() == frozenset()                 # nothing scanned yet
    sc.scan_now(TODAY)
    assert sc.symbols() == {"AAA"}
    now["t"] = datetime(2026, 10, 14, 13, 35, tzinfo=timezone.utc)
    assert sc.symbols() == frozenset()                 # yesterday's list never leaks
