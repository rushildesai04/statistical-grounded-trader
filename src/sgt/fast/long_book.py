"""Live paper LONG book (owner 2026-11-10, option A "longs first"): the trader's long setups traded on paper as they
happen, texted, listed before shorts. Paper only, no orders.

How it trades: every minute, for each research alert of the last WATCH + a few minutes (including alerts the SHORT
skipped for news or beta >= 3), the tested detectors and fill simulator (`sgt.fast.patterns`, the code of
`prereg_long_setups_2026-11-10`) run on today's CLOSED 1-minute bars. The simulator never looks ahead, so the live entry
and exit are what the test computes on the same bars. One long per alert: the first long setup to fill (ties: bull flag,
red-candle break, break of structure, red-candle close). A halt after the alert (no bar within 5 minutes) uses the
reopen versions (L7). Live bars are IEX (SIP is 15 minutes late on this plan); the OFFICIAL result (nightly, settle)
replays the live trade's own order on SIP bars, as the fade's estimate vs settle (2026-11-11; it was the shadow's
re-detection of the setup before).

Honest label: every long setup LOST on past alerts (-1.2% to -2.8% per trade); this book exists to show live whether
that holds. Respects PAUSE / DISARM; no entry after the last entry minute; at most MAX_OPEN open longs.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from time import monotonic
from typing import Callable

import pandas as pd

from sgt.fast import calc_v1, est_costs, fade, long_shadow, news
from sgt.fast import patterns as pt
from sgt.fast.fade_texts import live_note, stop_lines
from sgt.fast.fade import ET
from sgt.ops import approvals, chartserver
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
LONG_KINDS = ("L5v2", "L8", "L5", "L4b", "L6", "L4a", "L7b", "L7a")     # tie order when two fill on the same bar
NAMES = {"L5v2": "bull flag breakout", "L5": "bull flag breakout (v1)", "L4b": "red-candle break",
         "L6": "break of structure", "L8": "base break",
         "L4a": "red-candle close", "L7b": "halt reopen: red-candle break", "L7a": "halt reopen: red-candle close"}
TESTED_N = {"L4a": 2072, "L4b": 754, "L5": 247, "L6": 1149, "L7a": 77, "L7b": 31,     # prereg_long_setups training
            "L5v2": 199,                                                                   # prereg_flag_v2 training
            "L8": 255}       # bot replay, training trades the book still takes: at or under the cost cap and not while
#                              our short is open (prereg_long_cost_cap / prereg_long_while_short, 2026-11-20)
TESTED_WIN = {"L8": 0.37}     # share of those 255 trades that made money; no win rate is stored for the other setups
# 2026-11-19 (owner: "explore the problem and find a fix ... add on top of the complexity and cases for the LONG book";
# docs/findings/long_cases_2026-11-19.md). The setups the LIVE book takes. Until this date it took the first of the
# one-minute setups to fill (L4a/L4b first red candle, L6 break of structure, L7 halt reopen) with a stop at one
# candle's low: 45% of those trades were stopped in the entry minute and 15.6% won (-2.07% / -2.32% a trade,
# training / held-out). The base break (patterns.base_break: wait for some minutes in a narrow range after the alert,
# buy the close above it and above VWAP, stop under the whole base) won 36% / 33% and lost -0.79% / -1.12%; found on
# the free feed's bars and traded on the tape (the live path) -0.82% / -1.29%. On the alerts where NO base forms the
# one-minute setups lost the most (-2.46% / -2.44%): waiting for the base is what skips them. Not yet a winner after
# costs: it runs at test size until live trades decide. The one-minute setups are not deleted: they stay in the
# nightly shadow (long_shadow) and can be put back by adding their kinds here.
# 2026-11-19 night, the whole bot replayed (scripts/long_bot_replay.py, docs/findings/long_bot_replay_2026-11-19.md):
# with the real tick loop on free-feed candles and official results the base break makes -0.89% / -1.01% a trade
# (training / later period) and -1.85% on November's alerts, against -1.29% / -1.83% / -2.54% for the one-minute
# setups: about a third of the dollars lost, never a profit. The base's width cap is 4% since then (patterns).
LIVE_KINDS = ("L8", "L5v2")
# The rule set a decision was made under, on every line of the decision record (audit 2026-11-22). Change it whenever
# a setup, the entry moment, a gate, the stop, the target or the hold changes.
RULE_VERSION = ("long 2026-11-20: base break L8 + bull flag L5v2, bought at the break candle's close, cost cap 1%, no long "
                "while our short is open, 2R, 30 min | restart rule 2026-11-22: a fill on a candle that began before the "
                "listener was back is missed")      # live rule unchanged since 2026-11-20; see fade.RULE_VERSION's note
RULES_BASE = "B1"                    # long_trades.rules of a base-break long: stop under the base, 2R, HOLD; no red-candle
                                     # exit (that exit belongs to the one-minute setups it was tested on)
LIVE_WATCH = max(pt.WATCH, pt.BASE_WATCH)   # how long after its start bar an alert can still produce a setup
MAX_OPEN = 40
GRACE = pd.Timedelta(minutes=pt.TRIGGER_BARS + 2)          # after WATCH: the last setup's order can still fill
MARGIN = pd.Timedelta(minutes=15)                           # candidates outlive the window so the "none" marker lands
# 2026-11-10: after the 11:45 deploy the book booked KULR's 10:39 fill and texted entry + exit at once. A fill whose
# bar started longer ago than this was not seen live (restart, outage): recorded as 'missed', never traded or texted.
# Normal detection is the fill bar's close + one 30 s pass, well inside it. A missed fill is never scored officially.
LIVE_LAG = pd.Timedelta(minutes=5)
# Owner 2026-11-20 ("the main thing was being able to get in at the right time, which is not something we are doing
# optimally for LONGs"; docs/findings/long_entry_timing_2026-11-20.md): a base break is a market order by rule, fixed
# the moment its candle closes, yet the book booked and texted it only when the NEXT feed candle had closed: all 23
# live longs since 2026-11-11 were booked 62 to 88 s after the minute of the price in their text. On the training trades of the live configuration
# the same buy one minute later made 0.46 points less per trade (403 trades), and the break candle's close was nearer
# the tape's price at that moment than the feed's next open (0.43% against 0.61% off). So the buy is booked when the
# break candle closes, at that close, while it is this fresh; the official result is the tape's price in that minute
# (``_score``: the same market order, replayed from the minute the candle closed). Seen later than this (a restart, a
# feed outage): the old path, the feed's next candle.
AT_CLOSE = pd.Timedelta(seconds=90)
# prereg_long_risk_2026-11-12 addendum A2 (owner chose "both, test the combination first"; it passed): size each long
# by its risk, w = min(1, RISK_D50 / stop distance), and sell at the next open when the fill candle closes below the
# entry (patterns.simulate red_exit). Training per dollar -1.88% vs -2.29%, held-out -1.91% vs -2.37%: still losing.
RISK_D50 = 0.02679                   # training median of (entry - stop) / entry, fixed in the prereg
BOOK = "long book (paper)"           # sgt.learning.live_monitor book name (drift scale)
RULES = "A2"                         # long_trades.rules for trades opened under these rules: the red exit applies only to
                                     # them, live and official, so trades opened before the deploy keep their own rule


def risk_weight(entry: float, stop: float) -> float:
    """R: share of the full size for a long with this stop distance (never above 1; no positive risk -> 0)."""
    d = (entry - stop) / entry if entry > 0 else 0.0
    return min(1.0, RISK_D50 / d) if d > 0 else 0.0


# 2026-11-19 (owner: "act on it"; adversarial pass of that day): on the official tape the book's rules win 15.6% of
# their trades and need 32.6% to break even (held-out 13.9% vs 31.2%); live 17 trades, -2.2% each. No stop, target
# or size SHAPE closes that: tight-stop trades lose less per dollar than wide-stop ones in both samples (-1.6% vs
# -2.4% / -2.8%), so the risk weight below stays as tested. What changes is the exposure: until the book is PROVEN
# by the owner's own standard (playbook section 7: PROOF_N settled live trades and the 95% interval of their mean
# net above zero) a long is a TEST-size trade, at most PROBE_USD (x the risk weight x the drift scale). Every long
# still trades, is texted and is shown; the per-trade percentages that the proof standard reads do not depend on
# size. The owner can set another amount with the flag (dollars); a value that cannot be read never raises size.
PROBE_USD, PROBE_FLAG, PROOF_N = 500.0, "long_probe_usd", 300
PROBE_MIN, PROBE_MAX = 10.0, 100_000.0


def proven(db: OpsStore) -> bool:
    """The proof standard for this book: at least PROOF_N settled live longs whose mean net per trade has a 95%
    interval (mean -/+ 1.96 standard errors) entirely above zero."""
    with db.tx() as c:
        x = [float(r[0]) for r in c.execute("SELECT net FROM long_trades WHERE status='settled' AND net IS NOT NULL")]
    if len(x) < PROOF_N:
        return False
    m = sum(x) / len(x)
    var = sum((v - m) ** 2 for v in x) / (len(x) - 1)
    return m - 1.96 * math.sqrt(var / len(x)) > 0


def probe_usd(db: OpsStore) -> float | None:
    """The most dollars one long may put to work while the book is unproven; None once it is proven (full size)."""
    if proven(db):
        return None
    raw = db.flag(PROBE_FLAG)
    if raw is None or not str(raw).strip():
        return PROBE_USD
    try:
        v = float(raw)
    except ValueError:
        v = math.nan
    if not (math.isfinite(v) and PROBE_MIN <= v <= PROBE_MAX):
        log.error("long test-size flag is %r: using $%.0f", str(raw)[:20], PROBE_USD)
        return PROBE_USD
    return v


def full_weight(db: OpsStore, s: pt.Setup, t: pt.Trade, v: float) -> float:
    """What the fill would get at full size: the SIZE setting x the risk weight x the drift scale (live_monitor)."""
    from sgt.learning import live_monitor
    return fade.trade_weight(db, v) * risk_weight(t.entry, s.stop) * live_monitor.scale(db, BOOK)


def weight(db: OpsStore, s: pt.Setup, t: pt.Trade, v: float) -> float:
    """Share of the long book for this fill: the full-size weight, with the SIZE setting capped at the test size
    while the book is unproven (``probe_usd``)."""
    from sgt.learning import live_monitor
    base, cap = fade.trade_weight(db, v), probe_usd(db)
    if cap is not None:
        base = min(base, cap / v) if v > 0 else 0.0
    return base * risk_weight(t.entry, s.stop) * live_monitor.scale(db, BOOK)
LiveBars = Callable[[str, datetime], pd.DataFrame]          # (symbol, now) -> today's 1-minute bars (ts = start, UTC)


def rth(f: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Today's regular-session bars that have CLOSED by ``now`` (the research uses RTH bars only)."""
    if f is None or f.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    et = f.ts.dt.tz_convert(ET)
    m = et.dt.hour * 60 + et.dt.minute
    keep = (m >= 9 * 60 + 30) & (m < 16 * 60) & (f.ts + pt.BAR <= pd.Timestamp(now))
    return f[keep].sort_values("ts").reset_index(drop=True)


def candidates(db: OpsStore, now: datetime) -> list[dict]:
    """Today's research alerts still inside their long window, with no long trade yet."""
    session = now.astimezone(ET).date().isoformat()
    since = iso(now - (LIVE_WATCH + GRACE + MARGIN + pd.Timedelta(fade.MAX_ENTRY_DELAY)
                       + long_shadow.REOPEN_MAX).to_pytimedelta())
    with db.tx() as c:
        rows = c.execute(
            "SELECT id, symbol, session, posted_at, status, reason, via, recovered_at FROM fade_trades f WHERE "
            "session=? AND posted_at>=? "
            "AND " + long_shadow.QUALIFY + " AND " + long_shadow.FIRST + " AND NOT EXISTS (SELECT 1 FROM long_trades t "
            "WHERE t.fade_id=f.id) ORDER BY id",
            (session, since, *long_shadow.QUALIFY_ARGS, *long_shadow.QUALIFY_ARGS)).fetchall()
    return [dict(r) for r in rows]


# Owner 2026-11-17 (decision 4, found with the replay lab on MPU: buy 1.51, stop 1.50, target 1.53 on a stock that
# costs 1.3% to trade): a long whose 2R target, IF HIT, does not beat the estimated round-trip cost cannot win. On the
# stored long-book test 211 of 2,242 training trades (9%) were such trades: none won except one gap fill, mean -1.26%;
# held-out 52 of 724, -1.20% (docs/findings/prereg_long_nowin_2026-11-17.md). Such a fill is not taken; the book
# takes the next setup of that alert that can win, if one fills.
def no_win(symbol: str, s: pt.Setup, t: pt.Trade) -> bool:
    """True when the 2R target would not beat the estimated cost of the trade. No cost estimate -> False (not judged)."""
    c = est_costs.cost("long", symbol, t.entry_ts.to_pydatetime(), t.entry)
    return c is not None and t.entry > 0 and pt.RR * (t.entry - s.stop) / t.entry <= c


# Owner 2026-11-20 (decision A after the SVRE loss; docs/findings/prereg_long_cost_cap_2026-11-20.md): the long rules
# are break-even before costs in every cost band, so where the estimated round-trip cost is highest the loss is
# largest. Bot replay, trades above 1% against the rest: training -1.24% (n 111) against -0.53% (n 395), held-out
# -1.58% (n 33) against -0.61% (n 117), p 0.17; November's 21 trades the other way. With the gapper spread model
# "above 1%" is a stock under $5 bought before 10:30 ET. Such a fill is NOT bought and not texted: the alert's long is
# recorded as ``costly`` with its entry, scored each night on the official tape like a watched long (never in a total),
# and the count is reread at COST_REREAD. As in the test, the alert gets no other long. It makes the book lose less;
# it does not make it a winner (the kept trades lost -0.53% / -0.61%).
COST_CAP, COST_REREAD = 0.01, 50
COST_TESTED = -0.0158                  # held-out mean net of the trades above the cap (what the skipped ones lost)


def costly(symbol: str, t: pt.Trade) -> bool:
    """True when the estimated round-trip cost of this fill is above COST_CAP. No cost estimate -> False (not judged)."""
    c = est_costs.cost("long", symbol, t.entry_ts.to_pydatetime(), t.entry)
    return c is not None and c > COST_CAP


# Owner 2026-11-20 (JMIA: short from $2.78 and, twenty minutes later, long the same stock; his rule "no long while our
# short on it is open"; docs/findings/prereg_long_while_short_2026-11-20.md). A real account cannot hold both sides of
# one stock: the buy would close the short. Bot replay, longs taken while the short is open against the others:
# training -0.95% (n 179) against -0.54% (n 327), held-out -1.47% (n 64) against -0.35% (n 86), p 0.08 (PASS as
# registered, not proof). A fill on a stock whose fade is open is NOT bought and not texted: recorded as ``short_open``
# with its entry, scored each night like a watched long (never in a total), reread at SHORT_REREAD; the alert gets no
# other long (as tested). With the cost cap about half the longs remain (-0.26% / -0.23% a trade): less loss, no profit.
SHORT_TESTED, SHORT_REREAD = -0.0147, 50      # held-out mean net of the longs taken while the short was open


def short_open(db: OpsStore, symbol: str, session: str) -> bool:
    """True while the short book holds an open fade on this stock today."""
    with db.tx() as c:
        return c.execute("SELECT 1 FROM fade_trades WHERE symbol=? AND session=? AND status='open' LIMIT 1",
                         (symbol, session)).fetchone() is not None


Fill = tuple[pt.Setup, pt.Trade]


def live_setups(f: pd.DataFrame, row: dict) -> list[pt.Setup]:
    """The setups the live book may take for one alert on the closed bars ``f``: the start bar and the halt handling
    of the shadow (long_shadow.setups), the base break from that start bar, kept to LIVE_KINDS."""
    found, start = long_shadow.setups(f, {**row, "status": "live"})     # halts decided from the bars, as the shadow
    if start is None:
        return []
    return [s for s in found + pt.base_break(f, start) if s.kind in LIVE_KINDS]


def fills(f: pd.DataFrame, row: dict, latest: pd.Timestamp) -> tuple[Fill | None, Fill | None]:
    """(the first long setup to fill that can win, the first one that filled but cannot); each None when there is
    none yet. Ties on the fill bar go by LONG_KINDS."""
    ok, dead = [], []
    for s in live_setups(f, row):
        if s.side != "long":
            continue
        t = pt.simulate(f, s, latest=latest)
        if t is not None:
            (dead if no_win(row["symbol"], s, t) else ok).append((t.entry_ts, LONG_KINDS.index(s.kind), s, t))
    pick = lambda xs: min(xs, key=lambda x: (x[0], x[1]))[2:] if xs else None  # noqa: E731
    return pick(ok), pick(dead)


def at_close(f: pd.DataFrame, row: dict, now: datetime, latest: pd.Timestamp) -> Fill | None:
    """The base break whose candle has just closed, bought now at that close (see AT_CLOSE). None when the candle
    closed longer ago than AT_CLOSE, when the feed already shows a later candle (``fills`` decides then), when another
    long order of this alert is still pending (the first to fill is taken, as tested), or when the buy cannot beat
    its cost (``no_win``: the feed's own fill is judged later, as before)."""
    from sgt.fast import long_armed

    live = [s for s in live_setups(f, row) if s.side == "long" and long_armed.pending(f, s, now, latest)]
    if len(live) != 1 or live[0].kind != "L8" or live[0].trigger is not None:
        return None
    s = live[0]
    if pd.Timestamp(now) - s.ready > AT_CLOSE:
        return None
    ref = long_armed.reference(f, s)
    if ref is None or not ref > s.stop:
        return None
    t = pt.Trade(s.kind, "long", s.ready, ref, s.ready, ref, "open", 0.0)
    return None if no_win(row["symbol"], s, t) else (s, t)


def at_close_line(s: pt.Setup, now: datetime) -> str:
    """The LONG IN card's line for a buy booked when the break candle closed (it starts with a newline)."""
    ago = max(0, int((pd.Timestamp(now) - s.ready).total_seconds()))
    return f"\n⏱ Break candle closed {s.ready.tz_convert(ET):%H:%M:%S} ET ({ago}s ago): buy at market now"


def first_fill(f: pd.DataFrame, row: dict, latest: pd.Timestamp) -> Fill | None:
    """The first long setup to fill that the book would take (None = none yet)."""
    return fills(f, row, latest)[0]


# Owner 2026-11-13 (option A "measure-only"): the long book is a tested loser in every slice (A2 training -2.09% per
# trade, win 14%; every time-of-day bucket, setup and stop width negative) and the owner's bull flag and BoS rules also
# failed. Flag ``long_off``: "0" or absent = on, anything else = off (fail closed: no new longs). Open longs
# still exit and settle; the nightly long shadow (long_shadow) keeps scoring every alert, so nothing stops being
# measured. Back on only by the owner's word, for a rule that passed its pre-registered test.
# Owner 2026-11-17 (decision 1B: "measure-only + one 'seen, not traded' text"): while off, the book still WATCHES. The
# long it would have opened is recorded with status ``seen`` and texted once as seen, not traded: no position, no
# exit text, never in a total (every total reads open / est_closed / settled). The nightly settle scores what it
# would have made on official bars (the row stays ``seen``), so the measurement is of the book's own rule.
OFF_FLAG = "long_off"
SEEN, NOWIN = "seen", "nowin"       # long_trades.status: watched while off / filled but could not beat costs
COSTLY = "costly"                   # filled, not bought: estimated cost above COST_CAP (owner 2026-11-20)
SHORT_OPEN = "short_open"           # filled, not bought: our own short on the stock was open (owner 2026-11-20)
WATCHED = (SEEN, COSTLY, SHORT_OPEN)   # never a trade, never in a total; scored nightly on the official tape


def is_off(db: OpsStore) -> bool:
    v = db.flag(OFF_FLAG)
    return v is not None and v != "0"


def _window_over(row: dict, now: datetime) -> bool:
    posted = pd.Timestamp(row["posted_at"]).tz_convert("UTC")
    return pd.Timestamp(now) >= posted + pd.Timedelta(fade.MAX_ENTRY_DELAY) + long_shadow.REOPEN_MAX + LIVE_WATCH + GRACE


# 2026-11-19: with the base break an alert that has not traded stays a candidate for its whole window (before, 87% of
# alerts had a one-minute fill within minutes and left the list), and every candidate costs one bar request a pass.
# The busiest stored day (2026-11-11, 55 alerts) had 27 alerts inside one window: 108 requests a minute from this loop
# alone on a data key the short book's stop checks share. Bounded: at most MAX_LOOKS candidates are read per pass, the
# ones not read for the longest first. Up to MAX_LOOKS candidates (every day but two of the last eleven) nothing
# changes; above it each candidate is read every second pass (every third above 2 x MAX_LOOKS).
MAX_LOOKS = 15
_looked: dict[int, datetime] = {}      # fade id -> when the book last read its bars (this process)


def _turn(rows: list[dict], now: datetime) -> list[dict]:
    """The candidates to read this pass: all of them up to MAX_LOOKS, else the MAX_LOOKS not read for the longest
    (never read first; ties by id), in id order."""
    ids = {r["id"] for r in rows}
    for k in [k for k in _looked if k not in ids]:
        del _looked[k]
    pick = rows
    if len(rows) > MAX_LOOKS:
        never = datetime.min.replace(tzinfo=now.tzinfo)
        pick = sorted(sorted(rows, key=lambda r: (_looked.get(r["id"], never), r["id"]))[:MAX_LOOKS], key=lambda r: r["id"])
        log.info("long book: %d candidates, reading %d this pass", len(rows), len(pick))
    for r in pick:
        _looked[r["id"]] = now
    return pick


def live_from(row: dict, up_since: datetime | None) -> pd.Timestamp | None:
    """Since when the book could act on this alert in this process: the later of the alert's recovery (a replay
    after a restart, ``sgt.fast.alert_recovery``) and the listener's start. None = always (no restart involved).
    Listener recovery 2026-11-22 (owner: "never use a historical price as an executable entry"): a fill on a candle
    that began before this moment was not watched live, so it is ``missed``, as LIVE_LAG already says for fills more
    than five minutes old. For an alert handled live both marks are before the alert, so nothing changes."""
    marks = [pd.Timestamp(t) for t in (row.get("recovered_at"), up_since) if t]
    return max(m if m.tzinfo else m.tz_localize("UTC") for m in marks) if marks else None


SWEEP_EVERY = 15.0      # seconds: while candidates are being read, open longs are re-checked at least this often


def tick(db: OpsStore, bars: LiveBars, send, now: datetime | None = None, arm=None, rule=None,
         up_since: datetime | None = None, on_sweep=None) -> int:
    """One pass: advance the open longs FIRST, then look for new ones, then advance again when one was opened (its
    first exit check, in the pass that opened it, as before). Open longs first (operational risk review 2026-11-23):
    each candidate costs a bar request (up to MAX_LOOKS a pass, 5 s timeout each) and the exit check of the open
    longs used to wait behind all of them; they are also re-checked whenever SWEEP_EVERY has gone by meanwhile.
    ``on_sweep()`` is called at each exit check. Returns the number of longs opened. A base break is opened
    when its candle closes (``at_close``, owner 2026-11-20), any other fill when the feed shows it. ``arm`` (a text
    sender, owner 2026-11-18): a setup whose order is still pending is texted once BEFORE it fills
    (``sgt.fast.long_armed``); None = no armed texts. ``rule`` (a text sender): the trader's rule is texted when it would leave a long
    (``sgt.fast.trader_rule``); None = recorded without a text. The book's own fills are the same either way."""
    fixed, now = now, now or utcnow()

    def sweep(when: datetime) -> float:
        done = None
        if on_sweep is not None:
            try:
                done = on_sweep()                          # may hand back what to call when the check is over
            except Exception:  # noqa: BLE001 - a measurement; the check itself goes on
                log.exception("sweep hook failed")
        try:
            _advance(db, bars, send, when)
        finally:
            if callable(done):
                try:
                    done()
                except Exception:  # noqa: BLE001 - a measurement
                    log.exception("sweep hook failed")
        return monotonic()

    last, opened = sweep(now), 0
    for row in _turn(candidates(db, now), now):
        if monotonic() - last >= SWEEP_EVERY:
            last = sweep(fixed or utcnow())
        try:
            if db.paused() or approvals.disarmed(db, now):
                continue
            off = is_off(db)                               # measure-only: watched and texted as seen, never opened
            if not off:
                with db.tx() as c:
                    n_open = c.execute("SELECT COUNT(*) FROM long_trades WHERE status='open'").fetchone()[0]
                if n_open >= MAX_OPEN:
                    continue
            raw = bars(row["symbol"], now)
            f = rth(raw, now)
            latest = long_shadow._latest(row["session"])
            hit, dead = fills(f, row, latest) if len(f) else (None, None)
            now_line = ""
            if hit is None and len(f):
                hit = at_close(f, row, now, latest)        # owner 2026-11-20: bought when the break candle closes
                now_line = at_close_line(hit[0], now) if hit else ""
            if hit is None:
                if _window_over(row, now):                 # nothing the book takes: say whether a no-win fill was why
                    _insert(db, row, *(dead or (None, None)), NOWIN if dead else "none", now)
                elif arm is not None and len(f) and not short_open(db, row["symbol"], row["session"]):
                    _arm(db, row, f, now, off, arm, matrix_line(raw, now))
                continue
            s, t = hit
            since = live_from(row, up_since)
            stale = pd.Timestamp(now) - t.entry_ts > LIVE_LAG
            if stale or (since is not None and t.entry_ts < since):
                # the decision line says which rule made it "missed", so the restart rule's cases can be counted
                why = "fill older than 5 min when seen" if stale else "fill candle began before the listener was back"
                _insert(db, row, s, t, "missed", now, missed_why=why,
                        live_since=iso(since.to_pydatetime()) if since is not None else None)
                log.info("long %s %s filled %s, before the book saw it: missed", row["symbol"], s.kind, t.entry_ts)
                continue
            if costly(row["symbol"], t):                   # owner 2026-11-20: not bought, not texted, scored nightly
                _insert(db, row, s, t, COSTLY, now)
                continue
            if short_open(db, row["symbol"], row["session"]):   # owner 2026-11-20: one account, one side
                _insert(db, row, s, t, SHORT_OPEN, now)
                continue
            if off:
                if _insert(db, row, s, t, SEEN, now):
                    send(seen_text(db, row["symbol"], s, t) + now_line + matrix_line(raw, now))
                continue
            if _insert(db, row, s, t, "open", now):
                opened += 1
                send(entry_text(db, row["symbol"], s, t, now_line + matrix_line(raw, now) + context_line(db, row["id"])))
        except Exception:  # noqa: BLE001 - one alert failing never stops the book; logged with its id
            log.exception("long candidate %s failed", row["id"])
    if opened:                                 # a long opened in this pass gets its first exit check in it, as before
        sweep(now)
    try:                                       # owner 2026-11-18: the trader's rule, tracked beside every long
        from sgt.fast import trader_rule

        trader_rule.track(db, bars, rule, now)
    except Exception:  # noqa: BLE001 - tracked beside the book; it never stops the book
        log.exception("trader's rule tracking failed")
    return opened


def context_line(db: OpsStore, fade_id: int) -> str:
    """What the news / filings / social sources said at the alert, as an extra line for the entry text ("" when
    nothing was read; owner 2026-11-19, ``sgt.fast.alert_context``). Never raises."""
    try:
        from sgt.fast import alert_context

        return alert_context.line(db, fade_id)
    except Exception:  # noqa: BLE001 - an extra line; the text itself must go out
        log.exception("context line failed")
        return ""


def matrix_line(raw: pd.DataFrame, now: datetime) -> str:
    """The trader's matrix as an extra line for a long text ("" when it cannot be read). Never raises."""
    try:
        from sgt.fast import trend_matrix

        ln = trend_matrix.line(trend_matrix.snapshot(raw, now))
        return f"\n{ln}" if ln else ""
    except Exception:  # noqa: BLE001 - an extra line; the text itself must go out
        log.exception("trader's matrix line failed")
        return ""


def _arm(db: OpsStore, row: dict, f: pd.DataFrame, now: datetime, off: bool, arm, extra: str = "") -> None:
    """Text a pending setup once, before it fills. Never lets a failure reach the book: logged."""
    try:
        from sgt.fast import long_armed

        msg = long_armed.note(db, row, f, now, off)
        if msg:
            arm(msg + extra)
    except Exception:  # noqa: BLE001 - the armed text is an extra; the book's own pass must go on
        log.exception("armed text for %s failed", row["id"])


def _insert(db: OpsStore, row: dict, s: pt.Setup | None, t: pt.Trade | None, status: str, now: datetime,
            **facts) -> bool:
    v = nav(db)
    w = weight(db, s, t, v) if t is not None else None          # SIZE x risk x drift, against the LONG book's value
    rules = RULES_BASE if s is not None and s.kind == "L8" else RULES
    with db.tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, "
            "entry_ts, est_entry, est_last, created_at, rules) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["symbol"], row["session"], s.kind if s else None, status, s.trigger if s else None,
             s.stop if s else None, target(s, t) if s else None, w, iso(t.entry_ts.to_pydatetime()) if t else None,
             t.entry if t else None, t.entry if t else None, iso(now), rules))
        done, rid = cur.rowcount == 1, cur.lastrowid
    if done:       # audit 2026-11-22: the decision record (what the book did with this alert, and on what facts)
        db.decide("long", rid, row["symbol"], None, status, None, f"{RULE_VERSION}; rules {rules}", fade_id=row["id"],
                  kind=s.kind if s else None, setup_ready=iso(s.ready.to_pydatetime()) if s else None,
                  trigger=s.trigger if s else None, stop=s.stop if s else None, target=target(s, t) if s else None,
                  entry=t.entry if t else None, entry_ts=iso(t.entry_ts.to_pydatetime()) if t else None, weight=w,
                  decided_at=iso(now), posted_at=row.get("posted_at"), via=row.get("via"),
                  recovered_at=row.get("recovered_at"), **facts)
    return done


def target(s: pt.Setup, t: pt.Trade) -> float:
    return t.entry + pt.RR * (t.entry - s.stop)


def _advance(db: OpsStore, bars: LiveBars, send, now: datetime) -> None:
    """Close open longs when the simulator's exit is final on closed bars: a stop or target, or a time exit once the
    whole hold window (or the session) has closed.

    The list of open longs is a read (2026-11-24): under ``tx()`` this exit check queued for the write lock before it
    looked at anything (0.43 s on 11-24 behind another block that was only reading)."""
    with db.read() as c:
        rows = [dict(r) for r in c.execute("SELECT t.*, f.posted_at, f.status AS fstatus FROM long_trades t JOIN "
                                           "fade_trades f ON f.id=t.fade_id WHERE t.status='open' ORDER BY t.id")]
    for r in rows:
        try:
            f = rth(bars(r["symbol"], now), now)
            if f.empty:
                continue
            entry_ts = pd.Timestamp(r["entry_ts"])
            t = _exit(f, r, entry_ts)
            if t is None:
                continue
            close = pd.Timestamp(default_close(r["session"]))
            final = t.reason != "time" or pd.Timestamp(now) >= min(entry_ts + pt.HOLD, close)
            if not final:
                _set(db, r["id"], "open", est_last=float(f.close.iloc[-1]))
                continue
            if _set(db, r["id"], "open", status="est_closed", est_exit=t.exit, est_exit_at=iso(t.exit_ts.to_pydatetime()),
                    est_last=t.exit, reason=t.reason):
                send(exit_text(db, r, t))
        except Exception:  # noqa: BLE001 - one trade failing never stops the book; logged with its id
            log.exception("long trade %s failed", r["id"])


def _exit(f: pd.DataFrame, r: dict, entry_ts: pd.Timestamp) -> pt.Trade | None:
    """Exit of an open long on closed bars: stop first, then the 2R target (on the fill bar too: 2026-11-11), else a
    time exit at the last bar within HOLD (final only once the window has closed)."""
    entry, stop, tgt = float(r["est_entry"]), float(r["stop"]), float(r["target"])
    stop_entry = r["trigger"] is not None
    path = f[(f.ts >= entry_ts) & (f.ts < entry_ts + pt.HOLD)]
    for k, b in enumerate(path.itertuples()):
        first = k == 0
        if b.low <= stop:
            px = stop if first and stop_entry else min(stop, b.open)
            return pt.Trade(r["kind"], "long", entry_ts, entry, b.ts, px, "stop", px / entry - 1)
        if b.high >= tgt:                    # fill bar reaching the target: filled there (patterns module doc)
            px = tgt if first else max(tgt, b.open)
            return pt.Trade(r["kind"], "long", entry_ts, entry, b.ts, px, "target", px / entry - 1)
        if first and r.get("rules") == RULES and b.close < entry:   # red fill candle (prereg_long_risk E): next open
            nxt = f[f.ts > b.ts]
            if nxt.empty:
                return None                  # the next bar has not closed yet (last entry 15:45: one always follows)
            px = float(nxt.iloc[0].open)
            return pt.Trade(r["kind"], "long", entry_ts, entry, nxt.iloc[0].ts, px, "red", px / entry - 1)
    if path.empty:
        return None
    last = path.iloc[-1]
    return pt.Trade(r["kind"], "long", entry_ts, entry, last.ts, float(last.close), "time", float(last.close) / entry - 1)


def default_close(session: str) -> datetime:
    from sgt.intraday.calendar import default_calendar
    return default_calendar().bounds(date.fromisoformat(session)).rth_close


def _set(db: OpsStore, rid: int, expect: str, **cols) -> bool:
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        done = c.execute(f"UPDATE long_trades SET {keys} WHERE id=? AND status=?",  # noqa: S608 - fixed column names
                         (*cols.values(), rid, expect)).rowcount == 1
    if done and ("status" in cols or "reason" in cols):          # a price mark alone (est_last) is not a decision
        db.decide("long", rid, None, expect, cols.get("status", expect), cols.get("reason"), RULE_VERSION,
                  **{k: v for k, v in cols.items() if k not in ("status", "reason")})
    return done


def nav(db: OpsStore, through: date | None = None) -> float:
    """Official long book value: each settled long's official net (settle) at its own weight; ``through`` = only
    sessions up to that date (the value before a later day's trades, as fade.nav)."""
    with db.tx() as c:
        rows = c.execute("SELECT weight, net FROM long_trades WHERE status='settled' AND net IS NOT NULL"
                         + (" AND session<=?" if through else "") + " ORDER BY id",
                         ((through.isoformat(),) if through else ())).fetchall()
    v = fade.NAV0
    for r in rows:
        v *= 1 + r["weight"] * r["net"]
    return v


STALE_DAYS = 3                                     # a closed long with no official SIP bars after this is void
SIP_DELAY = pd.Timedelta(minutes=20)               # free-plan SIP bars are 15 minutes late: score after close + this
SipBars = Callable[[str, str], pd.DataFrame]       # (symbol, session) -> that session's SIP RTH 1-minute bars (UTC ts)
Cost = Callable[[str, datetime, float], float]     # round-trip cost fraction at (symbol, entry time, entry price)
NO_FILL = "live order did not fill on SIP bars"


def settle(db: OpsStore, bars: SipBars, cost_fn: Cost, now: datetime | None = None) -> list[str]:
    """Official result of each closed live long (owner 2026-11-11, "real long scoring"): the live trade's OWN order
    replayed on the session's SIP bars by the tested simulator - from the live fill bar, with the live buy-stop trigger
    and stop; the 2R target from the SIP fill, as the rule sets it. The fade's method (official = SIP prices at the
    live entry). Before 2026-11-11 the official result was the shadow's SIP RE-DETECTION of the setup, which can be a
    different trade (KULR 11-10 lost live at the IEX trigger 2.155; the shadow's SIP trigger 2.115 won +1.63%); rows
    settled that way (no official entry stored) are re-scored once. The shadow stays as the rule's own measurement.
    Never left hanging (review F-1): no SIP bars within STALE_DAYS = void. A texted long whose order does not fill
    on SIP is NOT void (owner 2026-11-18: a trade that was texted happened): it stays ``est_closed`` with reason
    NO_FILL and ``sgt.fast.text_order.run`` (next in the nightly job) books the order a real account would have sent
    at the text."""
    now = now or utcnow()
    today = now.astimezone(ET).date().isoformat()
    with db.tx() as c:                    # final pass F-1: a long still open from an earlier session (listener down
        c.execute("UPDATE long_trades SET status='est_closed', reason='closed by settle (listener missed the exit)' "
                  "WHERE status='open' AND session<?", (today,))      # across the close) gets its official result
        rows = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, kind, status, trigger, stop, entry_ts, net, rules, reason FROM long_trades WHERE "
            "status='est_closed' OR (status='settled' AND entry IS NULL) OR (status IN (?,?,?) AND settled_at IS NULL) "
            "ORDER BY id", WATCHED)]
    out = []
    stale = (now.astimezone(ET).date() - timedelta(days=STALE_DAYS)).isoformat()
    for r in rows:
        if pd.Timestamp(now) < pd.Timestamp(default_close(r["session"])) + SIP_DELAY:
            continue                                   # the session's SIP bars are not complete yet
        try:
            line = _score(db, r, bars, cost_fn, now, stale)
        except Exception:  # noqa: BLE001 - one trade failing never blocks the others; logged, retried next run
            log.exception("long %s official result failed", r["id"])
            continue
        if line:
            out.append(line)
    return out


def _parallel(db: OpsStore, rid: int, v: dict) -> None:
    """Store the price-decimal-v1 figure of a long the settle has just booked (owner 2026-11-25, option A): its own
    columns, written once, after the registered result and in a statement that touches nothing else. Read only by
    the nightly arithmetic line. A failure here is logged and changes nothing about the settled trade."""
    cols = {k: v[k] for k in calc_v1.LONG_COLS if v.get(k) is not None}
    if not cols:
        return
    try:
        with db.tx() as c:
            c.execute(f"UPDATE long_trades SET {', '.join(k + '=?' for k in cols)} WHERE id=? AND status='settled' "  # noqa: S608
                      "AND calc_v1 IS NULL", (*cols.values(), rid))
    except Exception:  # noqa: BLE001 - a parallel figure only
        log.exception("price-decimal-v1 figure of long %s not stored", rid)


def _score(db: OpsStore, r: dict, bars: SipBars, cost_fn: Cost, now: datetime, stale: str) -> str | None:
    """One long's official result (see settle). None = nothing changed (bars not there yet)."""
    f = bars(r["symbol"], r["session"])
    seen = r["status"] in WATCHED                      # a watched long: scored for the record, never becomes a trade
    if f is None or not len(f):
        if r["session"] <= stale and _set(db, r["id"], r["status"], reason="no official SIP bars",
                                          **({"settled_at": iso(now)} if seen else {"status": "void"})):
            return f"{r['symbol']} {r['kind']} void"
        return None                                    # review F-1 (shadow): an outage day is retried, never final
    trigger = None if r["trigger"] is None else float(r["trigger"])
    s = pt.Setup(r["kind"], "long", pd.Timestamp(r["entry_ts"]), trigger, float(r["stop"]))
    t = pt.simulate(f, s, latest=long_shadow._latest(r["session"]), red_exit=r.get("rules") == RULES)
    if t is None:
        if seen:
            return f"{r['symbol']} {r['kind']} void" if _set(db, r["id"], r["status"], reason=NO_FILL, settled_at=iso(now)) else None
        # 2026-11-18 (owner: "you can't cut trades that already happened"): not void; sgt.fast.text_order books it
        if str(r.get("reason") or "").startswith(NO_FILL) and r["status"] == "est_closed":
            return None                                # already waiting for it
        waits = f"{NO_FILL}; live exit {r.get('reason') or 'unknown'}"       # the live exit is kept: text_order reads it
        return (f"{r['symbol']} {r['kind']} not filled on the full tape: scored as bought at the text"
                if _set(db, r["id"], r["status"], status="est_closed", reason=waits) else None)
    rt = float(cost_fn(r["symbol"], t.entry_ts.to_pydatetime(), t.entry))
    net = t.gross - rt
    if not _set(db, r["id"], r["status"], status=r["status"] if seen else "settled", entry=t.entry,
                fill_ts=iso(t.entry_ts.to_pydatetime()), exit=t.exit, exit_ts=iso(t.exit_ts.to_pydatetime()),
                exit_reason=t.reason, gross=t.gross, cost_rt=rt, net=net, settled_at=iso(now),
                **({} if seen else {"calc": calc_v1.PRIMARY})):
        return None
    if not seen and r["status"] != "settled":        # a fresh settle only (review 2026-11-25): the one-time re-score
        #                                                  of a row settled long ago must not give that old trade a v1
        #                                                  figure: nothing settled before the parallel figure is restated
        _parallel(db, r["id"], calc_v1.long(f, s, t, rt, latest=long_shadow._latest(r["session"]),
                                            red_exit=r.get("rules") == RULES))
    if r["status"] == COSTLY:
        return f"{r['symbol']} {r['kind']} not bought (cost above {COST_CAP:.0%}): would have made {net:+.2%}"
    if r["status"] == SHORT_OPEN:
        return f"{r['symbol']} {r['kind']} not bought (our short was open): would have made {net:+.2%}"
    if seen:
        return f"{r['symbol']} {r['kind']} seen, not traded: would have made {net:+.2%}"
    was = f" (re-scored, was {float(r['net']):+.2%})" if r["status"] == "settled" and r["net"] is not None else ""
    return f"{r['symbol']} {r['kind']} {net:+.2%}{was}"


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def entry_text(db: OpsStore, symbol: str, s: pt.Setup, t: pt.Trade, extra: str = "") -> str:
    """The LONG IN card, laid out like the fade's entry card (owner 2026-11-20: "why are the texts different"): the
    trade in plain words, its tested record, the setup, the money and the levels, the live record, then ``extra``
    (the caller's trend-check and at-the-alert lines, each starting with a newline) and the chart link last."""
    v = nav(db)
    usd = weight(db, s, t, v) * v                                      # the size actually booked (_insert)
    sized = " (sized to the stop)" if risk_weight(t.entry, s.stop) < 1 else ""
    full = full_weight(db, s, t, v) * v
    paper = f"(paper, test size; full size ${full:,.0f})" if full - usd > 0.5 else "(paper)"
    risk = (t.entry - s.stop) / t.entry
    out = min(t.entry_ts + pt.HOLD, pd.Timestamp(default_close(t.entry_ts.tz_convert(ET).date().isoformat())))
    name, win = NAMES.get(s.kind, s.kind), TESTED_WIN.get(s.kind)
    cost = est_costs.cost("long", symbol, t.entry_ts.to_pydatetime(), t.entry) or 0.0
    lines = [f"[SGT] ✅ {bold('LONG IN')} · {bold(f'BUY {symbol}')} @ {bold(f'${t.entry:.2f}')}",
             f"💬 Bought ${usd:,.0f} of {symbol}: we make money if it rises",
             "🎲 " + (f"Past win rate {bold(f'{win:.0%}')} · " if win is not None else "") + f"{name}: "
             + italic(f"avg {long_shadow.TESTED.get(s.kind, 0):+.1%}/trade over {TESTED_N.get(s.kind, 0):,} past trades")
             + " (live paper test)",
             "    " + italic("Past figures: a replay of past alerts on candles, no real fills. Averages, not a forecast "
                             "for this trade."),
             f"💵 ${usd:,.0f} {paper}{sized}",
             f"🛑 stop ${s.stop:.2f} ({-risk:.1%}; a trigger, not a cap) ≈ {_usd(-usd * (risk + cost))} · "
             f"🎯 target ${target(s, t):.2f} "
             f"(+{pt.RR * risk:.1%}) · ⏰ out {out.tz_convert(ET):%H:%M} ET"]
    with db.tx() as c:
        n, m = c.execute("SELECT COUNT(*), AVG(net) FROM long_trades WHERE status='settled'").fetchone()
    if n and m is not None:
        lines.append(f"📈 Live paper record: {n} trades, avg {m:+.1%}" + (" " + italic(live_note(m, "long"))
                                                                           if n < PROOF_N else ""))
    chart = news.chart_url(symbol)
    return "\n".join(lines) + extra + (f"\n🔗 {chart}" if chart else "")


def seen_text(db: OpsStore, symbol: str, s: pt.Setup, t: pt.Trade) -> str:
    """The one text of a watched long while the book is measure-only: what it would have done, plainly not a trade."""
    risk = (t.entry - s.stop) / t.entry
    out = min(t.entry_ts + pt.HOLD, pd.Timestamp(default_close(t.entry_ts.tz_convert(ET).date().isoformat())))
    return (f"[SGT] 👀 LONG SEEN, not traded: {symbol} @ ${t.entry:.2f}\n"
            f"Setup: {NAMES.get(s.kind, s.kind)} · stop ${s.stop:.2f} ({-risk:.1%}) · target ${target(s, t):.2f} "
            f"(+{pt.RR * risk:.1%}) · would be out by {out.tz_convert(ET):%H:%M} ET\n"
            f"Measure-only (your choice): no position, not in the totals. What it would have made is on the site "
            f"tonight.\nTested on past alerts: {long_shadow.TESTED.get(s.kind, 0):+.1%}/trade over "
            f"{TESTED_N.get(s.kind, 0):,} trades")


EXIT_WHY = {"stop": "🛑 stop triggered", "target": "🎯 target hit", "time": "⏰ time exit",   # 2026-11-25: never "capped"
            "red": "🔻 fill candle closed red, out at the next open"}


def _day(db: OpsStore, session: str) -> tuple[int, float]:
    """(longs closed today, their estimated dollars after estimated costs): the LONG OUT card's day line."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, entry_ts, est_entry, est_exit, net, weight FROM long_trades WHERE session=? "
                         "AND status IN ('est_closed','settled')", (session,)).fetchall()
    v, n, usd = nav(db), 0, 0.0
    for r in rows:
        g = r["net"] if r["net"] is not None else est_costs.net("long", r["symbol"], r["entry_ts"], r["est_entry"],
                                                                r["est_exit"])
        if g is not None:
            n, usd = n + 1, usd + (r["weight"] or fade.WEIGHT) * v * g
    return n, usd


def exit_text(db: OpsStore, r: dict, t: pt.Trade) -> str:
    """The LONG OUT card, laid out like FADE OUT (owner 2026-11-20): result first, the two prices and why it closed,
    what the price did, the day so far, the links. Quoted after the estimated round-trip spread (2026-11-16)."""
    cost = est_costs.cost("long", r["symbol"], dict(r).get("entry_ts"), t.entry) or 0.0
    net = t.gross - cost
    usd = (r["weight"] or fade.WEIGHT) * nav(db) * net               # final pass F-2: the long book's own value
    mark = "🛑" if t.reason == "stop" else ("🟢" if net > 0 else "🔴")
    sym, both = r["symbol"], f"{_usd(usd)} ({net:+.1%})"
    lines = [f"[SGT] {mark} {bold('LONG OUT')} · {bold(sym)} · {'made' if net > 0 else 'lost'} {bold(both)}",
             f"Bought ${t.entry:.2f} → sold ${t.exit:.2f} · {EXIT_WHY.get(t.reason, t.reason)}"]
    move = t.exit / t.entry - 1
    lines.append("💡 Why: " + (f"price rose {move:.1%} while we were long" if move > 0 else
                              f"price fell {abs(move):.1%} while we were long" if move < 0 else "price did not move"))
    if t.reason == "stop" and dict(r).get("stop"):     # owner 2026-11-25: the stop's loss in its parts, never "capped"
        lines += stop_lines("long", t.entry, float(r["stop"]), t.exit, cost)
    session = dict(r).get("session")
    n_day, day_usd = _day(db, session) if session else (0, 0.0)
    if n_day:
        lines.append(f"📅 Today: {n_day} long{'s' if n_day != 1 else ''}, {bold(_usd(day_usd))}")
    chart = news.chart_url(sym)
    if chart:
        lines.append(f"📈 {chart}")
    page = chartserver.link()
    if page:
        lines.append(f"🖼 All trades: {page}")
    lines.append(italic(f"Paper, estimate after about {cost:.1%} costs (spread); official numbers in the midnight summary."
                        if cost > 0 else "Paper, estimate before costs; official numbers in the midnight summary."))
    return "\n".join(lines)


def today_line(db: OpsStore, session: str) -> str | None:
    """Digest TODAY line for the long book (estimates until settled)."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, kind, status, entry_ts, est_entry, est_exit, net, weight FROM long_trades WHERE "
                         "session=? AND status IN ('open','est_closed','settled','void') ORDER BY id",
                         (session,)).fetchall()
    # review F-2: a void long has no official result: say so instead of showing its estimate
    if not rows:
        return None
    bits = []
    for r in rows:
        g = r["net"] if r["net"] is not None else est_costs.net("long", r["symbol"], r["entry_ts"], r["est_entry"],
                                                                r["est_exit"])       # estimate: after est. costs
        state = "void" if r["status"] == "void" else ("open" if g is None else f"{g:+.1%}")
        bits.append(f"{r['symbol']} {NAMES.get(r['kind'], r['kind'])} {state}")
    return f"longs (paper) {len(rows)} trades: " + ", ".join(bits)


def seen_line(db: OpsStore, session: str) -> str | None:
    """Digest line for the longs WATCHED that day while the book was measure-only (never part of a total)."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, kind, net FROM long_trades WHERE session=? AND status=? ORDER BY id",
                         (session, SEEN)).fetchall()
    if not rows:
        return None
    bits = [f"{r['symbol']} {NAMES.get(r['kind'], r['kind'])}" + ("" if r["net"] is None else f" {r['net']:+.1%}")
            for r in rows]
    return f"longs seen, not traded ({len(rows)}; not in the total): " + ", ".join(bits)


def costly_line(db: OpsStore, session: str) -> str | None:
    """Digest line for the day's longs NOT bought because their cost was above the cap (never part of a total)."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, kind, net FROM long_trades WHERE session=? AND status=? ORDER BY id",
                         (session, COSTLY)).fetchall()
    if not rows:
        return None
    bits = [f"{r['symbol']} {NAMES.get(r['kind'], r['kind'])}" + ("" if r["net"] is None else f" {r['net']:+.1%}")
            for r in rows]
    return f"longs not bought, cost above {COST_CAP:.0%} ({len(rows)}; not in the total): " + ", ".join(bits)


def cost_check_line(db: OpsStore) -> str | None:
    """The cost cap against its own test, every night: how many longs it has kept the book out of, what they would
    have made on the official tape, the tested figure, and the fixed count at which the owner rereads it. It never
    changes the rule."""
    with db.tx() as c:
        n, m, since = c.execute("SELECT COUNT(*), AVG(net), MIN(session) FROM long_trades WHERE status=? AND net IS NOT "
                                "NULL", (COSTLY,)).fetchone()
    if not n:
        return None
    return (f"long cost check (longs not bought, since {since[5:]}, in no total): {n} at {m:+.1%} "
            f"(tested {COST_TESTED:+.1%}; reread at {COST_REREAD})")


def short_open_line(db: OpsStore, session: str) -> str | None:
    """Digest line for the day's longs NOT bought because our own short on the stock was open (never in a total)."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, kind, net FROM long_trades WHERE session=? AND status=? ORDER BY id",
                         (session, SHORT_OPEN)).fetchall()
    if not rows:
        return None
    bits = [f"{r['symbol']} {NAMES.get(r['kind'], r['kind'])}" + ("" if r["net"] is None else f" {r['net']:+.1%}")
            for r in rows]
    return f"longs not bought, our short was open ({len(rows)}; not in the total): " + ", ".join(bits)


def short_check_line(db: OpsStore) -> str | None:
    """The short-open rule against its own test, every night (as cost_check_line). It never changes the rule."""
    with db.tx() as c:
        n, m, since = c.execute("SELECT COUNT(*), AVG(net), MIN(session) FROM long_trades WHERE status=? AND net IS NOT "
                                "NULL", (SHORT_OPEN,)).fetchone()
    if not n:
        return None
    return (f"long short-open check (longs not bought, since {since[5:]}, in no total): {n} at {m:+.1%} "
            f"(tested {SHORT_TESTED:+.1%}; reread at {SHORT_REREAD})")


def record_line(db: OpsStore) -> str | None:
    with db.tx() as c:
        n, m = c.execute("SELECT COUNT(*), AVG(net) FROM long_trades WHERE status='settled'").fetchone()
    if not n:
        return None
    cap = probe_usd(db)
    # 2026-11-24: "until 300 live trades prove it" read as the book's review schedule. Two different rules: the SIZE
    # stays at the test amount until the owner's proof standard is met (``proven``); the paper REVIEW of the book is
    # the registered one (exec_baseline_2026-11-21 section 6), at its own counts.
    from sgt.fast import exec_book

    size = "" if cap is None else (
        f"; test size ${cap:,.0f} a long (size rule: until {PROOF_N} settled live trades with a 95% interval above "
        f"zero; the registered paper review is separate: early look at {exec_book.EARLY['long'][0]}, review at "
        f"{exec_book.REVIEW_N['long']} trades with no exclusion flag from {exec_book.REVIEW_FROM[5:]})")
    return (f"long book (paper) {n} trades, avg {m:+.2%}/trade, book ${nav(db):,.0f}; "
            f"tested {min(_live_tested()):+.1%} to {max(_live_tested()):+.1%}{size}")


def _live_tested() -> list[float]:
    """The tested per-trade results of the setups the live book takes now."""
    return [long_shadow.TESTED[k] for k in LIVE_KINDS if k in long_shadow.TESTED] or [0.0]
