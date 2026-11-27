"""Live alert-fade book (2026-11-03; rule frozen in docs/findings/prereg_alert_fade_2026-11-01.md).

Two layers, so the live book can never drift from the tested rule:

- LIVE (this module): an alert arrives -> research filters (cell type, 09:30-15:44 ET entry, $1-50, first
  event per symbol-session) -> ops gates (ARM for today, not paused, daily caps) -> a virtual short at the
  real-time (IEX) price, texted. A per-minute monitor texts the estimated stop (+20%) or 60-minute exit.
- OFFICIAL (``settle``): once SIP 1-minute bars are final, the caller's ``official`` function re-computes
  every trade with the research code itself (telegram_alert_test.attach_trades/primary_events +
  alert_fade_test.score). Only official nets move the book's NAV; estimates are display-only.

Entry permission (amendments F3/F4): ARMed day -> automatic; otherwise each event texts a request that enters
automatically at its deadline unless the owner replies NO (OK enters at once); the deadline keeps the entry inside
the tested 5-minute window; PAUSE / DISARM block it. Approved and auto-entered trades are officially scored from
their real entry time. Exits are always automatic.

Status: filtered (a research rule excluded it; does not block a later alert) | skipped (an ops gate
blocked the research event) | ask (approval texted, waiting) | pending (waiting for a first quote) | open |
est_closed | settled | void.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from typing import Callable

import pandas as pd

from sgt.fast import calc_v1, est_costs
from sgt.fast.fade_rule import (AVG_LOSS, AVG_WIN, BASE_EV, BASE_N, BASE_WIN, BASE_WIN_CI, ET, HOLD,  # noqa: F401
                                JUDGE_N, STOP, STOP_RATE, STOPPED_NET, STOPS, VETO, VETO_EXIT)
from sgt.fast.fade_texts import (CONF, CONF_LABEL, CONF_MAX, EXIT_WHY, FILING_HIST, PLAIN, TOD_HIST, _fmt,  # noqa: F401
                                 _qty, _usd, ask_text, borrow_note, confidence, news_group, out_text, signals_line,
                                 tod_hist)
from sgt.intraday.calendar import default_calendar
from sgt.ops import alert_log, approvals
from sgt.ops.sms_style import italic
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
# Mirrors telegram_alert_test.CELL_OF (cells only); tests/fast/test_fade.py asserts they stay equal.
CELLS = frozenset({"volume_increasing", "volume_spike", "gaining_volume", "gaining_momentum", "new_high",
                   "intraday_high", "testing_high", "price_level", "resumed", "news"})
REACTION = timedelta(seconds=10)
MAX_ENTRY_DELAY = timedelta(minutes=5)
FIRST_ENTRY, LAST_ENTRY = time(9, 30), time(15, 44)
PRICE_MIN, PRICE_MAX = 1.0, 50.0
SETTLE_AFTER = timedelta(minutes=60 + 20)       # SIP is free once 15 min old; 5 min margin
NAV0 = 100_000.0
WEIGHT = 0.05                                   # default short notional per trade, share of NAV
# Owner 2026-11-09: dollar size per trade is a setting (SMS ``SIZE 10000``; raising it needs a code). Unset = the
# 5%-of-NAV default above. Bounds: at least MIN_TRADE_USD, at most MAX_WEIGHT of the book per trade, and new
# entries stop while open shorts would exceed MAX_GROSS of the book (= the old 40 x 5% runaway bound).
SIZE_FLAG, MIN_TRADE_USD, MAX_WEIGHT, MAX_GROSS = "fade_trade_usd", 500.0, 0.25, 2.0
# Owner 2026-11-09: no limit on how many trades: every qualifying alert trades. The only bound left is a RUNAWAY
# GUARD on simultaneously open positions (a bug or a flood of duplicate alerts), far above anything seen live (max 5).
MAX_TRADES_DAY, MAX_OPEN = 10_000, 40
DAY_LOSS_STOP = -0.03                           # retired 2026-11-09 (amendment F6); see caps()
HEARTBEAT_STALE = timedelta(minutes=15)
ASK_MARGIN = timedelta(seconds=40)              # approval must land this long before the entry deadline
# Amendment F5 (owner decision 2026-11-04, prereg_fade_challengers: entering 2-4 min late loses most of the edge):
# not ARMed -> enter at the tested timing and text the trade with "NO <code> = exit now". True restores the
# F3/F4 ask-first flow (_ask), kept intact for rows created before F5 and for a reversal.
ASK_FIRST = False
# Amendment F6 (owner decision 2026-11-09, prereg_fade_stop_slippage_2026-11-09): trade NO-NEWS alerts only. With real
# stop fills no-news fades earned +1.70% / +1.77% per trade (training / held-out), news fades -0.20% / -0.60%. A
# news-type alert, or company news found at entry (the same lookup as the trade text), is filtered; an unknown news
# lookup does not block (the group "all" was still positive, +0.54% before 11:00).
NO_NEWS = True
NEWS_REASON = "news (no-news rule, amendment F6)"
# Amendment F7 (owner 2026-11-10, option A "longs first"): no short on a beta >= 3 name. Past alerts by beta (report
# only, beta from the year before each alert): >= 3 -2.14%/trade (n 76; no-news -6.67%, n 29), < 1.5 +1.86% (n 1,654).
HIGH_BETA_SKIP = 3.0
BETA_REASON = "beta >= 3 (no shorts on high beta, amendment F7)"
# Owner 2026-11-21 (system audit, finding 12: "yes for beta"): a short whose beta cannot be read is not taken, like a
# short whose borrow is unknown. Until now an unknown beta never blocked (3 shorts, all on 2026-11-10, the rule's first
# day). Unknown means the lookup failed or timed out on every pass of the entry window, OR the stock has fewer than 60
# daily returns (``sgt.fast.beta.MIN_OBS``): a recent listing has no beta and is therefore not shorted. Checked after
# the price, so an alert with no print or a price outside $1-50 keeps that reason. Unknown company news still does not
# block (owner: "leave for news").
BETA_UNKNOWN = "beta unknown at entry (no beta, no short)"
# Amendment F8 (prereg_fade_repeat_days_2026-11-12, deep review of the 11-12 losses): no short on a stock the channel
# alerted 1-7 calendar days earlier (a multi-day runner). No-news fades: kept +2.54% vs repeats -1.01% (training,
# n 1,018 / 317), held-out +2.55% vs -1.33% (n 371 / 133). The long book still treats these alerts as events.
NO_REPEAT, REPEAT_DAYS = True, 7
REPEAT_REASON = "alerted in the prior 7 days (multi-day runner, amendment F8)"
NO_PRINT_REASON = "no trade print within 5 min (halted or no quote)"
# Skips that show the alert had no tested entry bar (not an event); "no quote within 5 min" is the pre-11-09 wording.
NOT_EVENT_SKIPS = (NO_PRINT_REASON, "no quote within 5 min", "session closed before entry")
# Listener recovery 2026-11-22: the stock DID trade in the tested entry bar, but no print came after the listener was
# back (a recovered alert, or a restart with the alert waiting). An operational skip, like "alert received late": the
# alert stays an event (F8 and the long book count it); it is not evidence of a halt, so it is not in NOT_EVENT_SKIPS.
RESTART_NO_PRINT = "no trade print after the restart within 5 min of the alert"
# Owner 2026-11-25: "Substitute short entries are record-only. If the registered entry opportunity has already
# passed, a later free-feed print does not authorize a replacement trade. This decision follows the specification,
# not the outcomes of BUUU or GLXG." The registration enters at ONE bar, the first official minute at or after alert +
# 10 s; its five minutes bound how late that FIRST bar may be (recovery_verification_2026-11-23.md, section 1). The
# live book reads a free feed and used to enter on its first print inside those five minutes, also when that print
# came in a later minute than the registered one (4 of 34 live entries; in 2 the official tape had traded in the
# registered minute: BUUU 11-23, GLXG 11-24).
#
# What can be known at the moment of the entry (docs/findings/entry_eligibility_2026-11-25.md):
# - that the stock DID trade earlier: yes, when the free feed shows any trade (odd lots included) between the start of
#   the registered minute and the start of the current one. Its trades are on the official tape too.
# - that it did NOT trade earlier (a halt, a genuinely delayed first print: the case the registration still enters):
#   no. The free feed's silence is not the official tape's, and the official tape is free only 15 minutes later.
# So a live entry is decided INSIDE the registered minute or not at all. Later than that the alert is recorded,
# never shorted: "substitute" when earlier trading is confirmed, "unknown" when it cannot be (never called a halt).
# What the official bars showed is written the same night (``entry_official``), so the count of genuinely delayed
# first prints the live book could not confirm is known. Unchanged: the registered replay (settle, forward record,
# would-have: all from official bars, the first bar within five minutes); an alert with no free-feed print for five
# minutes (NO_PRINT_REASON, as before); approved / auto entries of the old ask-first mode; and the restart rules.
# An alert that itself ARRIVED after its registered minute had ended (delivery delay) is record-only too: the owner
# closed that question the same day ("record-only under the same registered-minute eligibility principle").
ENTRY_MINUTE = timedelta(minutes=1)
ENTRY_RULE = ("live-entry-v2 2026-11-25: decided inside the registered entry minute, else record-only "
              "(an alert delivered late included)")
SUBSTITUTE_REASON = "registered entry minute passed: no substitute entry on a later free-feed print (record-only)"
ELIG_REGISTERED = "inside the registered entry minute"
ELIG_SUBSTITUTE = "substitute: the stock traded earlier, in or after its registered entry minute"
ELIG_UNKNOWN = "unknown: earlier official trading cannot be confirmed or ruled out in time"
ELIG_EXEMPT = "later minute, outside this rule: "       # + approved entry / restart rules
# fade_trades.entry_wait (2026-11-24): why an entry had no price on its first pass, when the quote itself gave no reason
NO_PRICE_YET = "no price from the live quote yet"
RESTART_WAIT = "the listener restarted inside or after the entry bar: no entry on a print it cannot date"
# Record-only for recovered SHORT alerts (docs/findings/recovery_verification_2026-11-23.md, section 1). The registered
# rule enters at the first bar at or after alert + 10 s; its five minutes bound how late that FIRST bar may be. The
# live code also tolerates an alert that arrives late, and amendment F5 withdrew late entries on evidence (the edge
# is front-loaded: +0.97% at the alert, +0.14% two minutes late, -0.21% four minutes late). Nothing in the
# specification authorizes an entry after the listener was down, so an alert found by the replay is recorded and not
# entered: an operational skip, like "alert received late", so it stays an event (F8 and the long book count it).
# False = record only. True = the existing late rule decides (an entry then needs a print from after the recovery,
# inside the five minutes). Changing it is a decision about the trading rule: the owner's, by a change of its own.
RECOVERED_SHORT_ENTRIES = False
RECOVERED_ONLY = "recovered after a restart: recorded, not entered"
# Owner 2026-11-12 night (option A after the adversarial validation): new shorts OFF. The November forward record lost
# -3.44%/trade (win 39%, n 61; a 9-session stretch of held-out days is this bad 1.1% of the time) and two independent
# checks found no code or backtest bias; owner: shorting is deprioritized (borrowed shares, unlimited loss, margin).
# Flag ``fade_off``: "0" or absent = on, anything else = off (fail closed: no new shorts). Checked at the tested entry
# AFTER the news, beta and price checks, so an off row is exactly an alert the fade would have shorted: F8 still counts
# it and the long book still trades it. The forward record (alert_fade_forward, fade_challengers) keeps scoring every
# alert, so the edge is still measured while the book is off.
OFF_FLAG = "fade_off"
OFF_REASON = "fade off (owner 2026-11-12: November forward break; shorting deprioritized)"


def is_off(db: OpsStore) -> bool:
    v = db.flag(OFF_FLAG)
    return v is not None and v != "0"


# Owner 2026-11-16 ("pretend like its a real scenario"; interview answer "IBKR has shares"): a real account cannot short
# a stock its broker has no shares to lend. The paper book shorted every alert: of its 22 settled shorts, 7 had no
# shares at IBKR (nor at Alpaca) and 2 had fewer than the trade needed; together +$2,046 the book could never have
# earned (the 13 real ones lost about -$5,031; the book showed -$2,985). Rule: a short is taken only when the IBKR lendable list stamped on the alert (``borrow.BorrowBook``, a
# snapshot at most 3 hours old) shows at least the shares the trade needs. Unknown is no trade (fail closed). Checked at
# the tested entry after the news, beta and price checks, so such a row is a real alert the fade wanted to short: F8
# counts it, the long book still trades it, and the forward record keeps scoring every alert whatever the borrow.
BORROW_NONE = "no shares to borrow at IBKR (a real account could not short it)"
BORROW_FEW = "too few shares to borrow at IBKR for this size"
BORROW_UNKNOWN = "borrow unknown at entry (no fresh IBKR list): no short"
BORROW_REASONS = (BORROW_NONE, BORROW_FEW, BORROW_UNKNOWN)
# Alerts the short did not take although they were events in a tested cell with a priced entry bar.
EVENT_FILTERS = (NEWS_REASON, BETA_REASON, BETA_UNKNOWN, REPEAT_REASON, OFF_REASON, *BORROW_REASONS)


def no_shares(db: OpsStore, r, px: float) -> str | None:
    """Why a real account could not put this short on at ``px`` (one of BORROW_REASONS), or None when IBKR lends at
    least the shares the trade needs (its weight on the book's value, at the entry price)."""
    avail = dict(r).get("ib_avail")
    if avail is None:
        return BORROW_UNKNOWN
    if avail <= 0:
        return BORROW_NONE
    need = math.ceil((dict(r).get("weight") or WEIGHT) * nav(db) / px)
    return BORROW_FEW if avail < need else None

Quote = Callable[[str], "float | None"]
# A quote may also accept ``since=`` (set ``quote.since_aware = True``): then it returns None unless its print is at or
# after ``since``. The live IEX quote does; plain test doubles do not and are used as-is.


def entry_start(posted: datetime) -> datetime:
    """When the tested rule can enter: the start of the first 1-minute bar at or after alert + REACTION
    (telegram_alert_test.attach_trades: bar ts >= posted + 10 s). OMH review 2026-11-09: the live book used to short at
    the latest print at alert + 10 s, mid-minute, so an alert followed by a halt (no bar for 5+ minutes) was a live
    trade the backtest never had (prereg_fade_halt_audit_2026-11-09: those events lose -4.9% / -7.4% per trade)."""
    t = posted + REACTION
    floor = t.replace(second=0, microsecond=0)
    return t if t == floor else floor + timedelta(minutes=1)
Borrow = Callable[[str, datetime], dict]
NewsCheck = Callable[[str, datetime], "dict | None"]          # company-news flags at a time; None = unknown
BetaCheck = Callable[[str, datetime], "float | None"]         # the symbol's beta (sgt.fast.beta); None = unknown
BORROW_COLS = ("ib_avail", "ib_fee", "ib_snap", "alp_etb", "alp_shortable", "ib_received", "ib_read_at")
Official = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass(frozen=True, slots=True)
class LiveAlert:
    symbol: str
    alert_type: str
    posted_at: datetime                         # tz-aware
    channel: str
    message_id: int
    has_news: bool | None = None
    via: str | None = None                      # "push" | "poll" | "replay" (sgt.fast.alert_recovery); None = unknown
    recovered_at: datetime | None = None        # set for a replay: when the catch-up found it (tz-aware)


# The rule set a decision was made under, stored on every line of the decision record (audit 2026-11-22). Change it
# whenever a gate, the entry, the stop or the hold changes.
# 2026-11-23: the rules for an alert handled live are those of 2026-11-21, unchanged. The restart rules were added on
# 2026-11-22/23 without a new label (found in the post-merge check): decision lines written by runner v108 and v109
# (2026-11-22 23:24 ET to the deploy of this change) carry the label without the restart part; the effective versions
# are tabled in docs/findings/ops_risk_register_2026-11-23.md, section 2.
RULE_VERSION = ("fade 2026-11-21: F6 no-news, F7 beta<3 (unknown = no short), F8 no repeat 7d, borrow listed, $1-50, 20% stop, "
                "60 min | restart rules 2026-11-23: a recovered alert is recorded, not entered; no entry on a print from "
                "before the listener was back; the 5 min entry limit is not stretched by downtime | entry 2026-11-25: "
                "decided inside the registered entry minute, else record-only, late delivery included (not: approved, "
                "restart)")
RECORDED = ("status", "reason", "news_own", "beta")     # a row update that touches one of these is a decision


ALREADY = -1                                    # _insert: the alert is already on record (handled before)


def _insert(db: OpsStore, a: LiveAlert, session: str, status: str, reason: str, now: datetime) -> int | None:
    """The alert's row and its durable identifier (``alert_log``), in ONE transaction: either both are stored or
    neither. Returns the row id, None for a second alert of a stock that already has a live row today (recorded,
    no row), or ALREADY when this very alert was handled before (a second delivery: nothing is written)."""
    w = trade_weight(db) * drift_scale(db) if status in ("pending", "ask") else None   # fixed at entry; settle uses it
    k, rec = alert_log.key(a), a.recovered_at
    with db.tx() as c:
        if alert_log.find(c, k) is not None:
            return ALREADY
        old = alert_log.legacy_row(c, k)
        if old is not None:      # its row exists without an identifier (an older build wrote it): handled, never twice
            alert_log.put(c, k, a.posted_at, now, None, None, old["status"], alert_log.LEGACY, old["id"])
            return ALREADY
        if status != "filtered" and c.execute("SELECT 1 FROM fade_trades WHERE symbol=? AND session=? AND "
                                              "status!='filtered'", (a.symbol, session)).fetchone():
            db_dup = True
            alert_log.put(c, k, a.posted_at, now, a.via, rec, alert_log.DUPLICATE,
                          "the stock already has a live alert today", None)
        else:
            db_dup = False
            rid = c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status,"
                            " reason, weight, created_at, via, recovered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (a.symbol, session, a.alert_type, iso(a.posted_at), a.channel, a.message_id, status, reason,
                             w, iso(now), a.via, iso(rec) if rec else None)).lastrowid
            alert_log.put(c, k, a.posted_at, now, a.via, rec, status, reason, rid)
    how = {"via": a.via, "recovered_at": iso(rec) if rec else None,                  # the replay label, on the record
           "recovered_age_s": round((rec - a.posted_at).total_seconds(), 1) if rec else None}
    if db_dup:      # the stock already has a live row today: no second trade, but the alert is on the record
        db.decide("short", None, a.symbol, None, "duplicate", "the stock already has a live alert today", RULE_VERSION,
                  alert_type=a.alert_type, posted_at=iso(a.posted_at), received_at=iso(now), message_id=a.message_id,
                  **how)
        return None
    db.decide("short", rid, a.symbol, None, status, reason or None, RULE_VERSION, alert_type=a.alert_type,
              posted_at=iso(a.posted_at), received_at=iso(now), received_after_s=round((now - a.posted_at).total_seconds(), 1),
              weight=w, **how)
    return rid


def _no_row(db: OpsStore, a: LiveAlert, now: datetime, what: str) -> str:
    """An alert that gets no row by design (not a tested kind, a closed day): still on the durable record, once."""
    alert_log.note(db, alert_log.key(a), a.posted_at, now, a.via, a.recovered_at, alert_log.NONE, what)
    return what


def day_est_pnl(db: OpsStore, session: str) -> float:
    """Estimated NAV fraction realized today (closed estimates; open trades at their last mark), after estimated
    costs (2026-11-16, ``est_costs``: the texted day total used to be before costs)."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, weight, est_entry, est_entry_at, COALESCE(est_exit, est_last) px FROM "
                         "fade_trades WHERE session=? AND status IN ('open','est_closed') AND est_entry > 0",
                         (session,)).fetchall()
    nets = [(r["weight"], est_costs.net("short", r["symbol"], r["est_entry_at"], r["est_entry"], r["px"])) for r in rows]
    return sum(w * n for w, n in nets if n is not None)


def gate(db: OpsStore, session: str, now: datetime) -> str | None:
    """Ops reason that blocks a new AUTOMATIC entry, or None."""
    if not approvals.armed(db, now):
        return "paused" if db.paused() else "not armed"
    return caps(db, session)


def caps(db: OpsStore, session: str) -> str | None:
    """Risk caps that block any new entry (automatic or approved), or None."""
    with db.tx() as c:
        n_day = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=? AND status IN "
                          "('ask','pending','open','est_closed','settled')", (session,)).fetchone()[0]   # void: not a trade
        n_open, gross = c.execute("SELECT COUNT(*), COALESCE(SUM(weight), 0) FROM fade_trades WHERE status IN "
                                  "('ask','pending','open')").fetchone()
    if n_day >= MAX_TRADES_DAY:
        return f"daily cap {MAX_TRADES_DAY}"
    if n_open >= MAX_OPEN:
        return f"runaway guard: {MAX_OPEN} open"
    if gross + trade_weight(db) > MAX_GROSS + 1e-9:
        return f"runaway guard: {gross:.0%} of the book already short"
    # 2026-11-09 owner decision (prereg_alert_fade amendment F6): the day-loss stop is removed. The breaker study
    # (prereg_fade_dayflow_2026-11-08) found fades after two losses earned +2.18% (training), so stopping costs ~1.3%
    # per session, and it acted on noisy IEX estimates. Kept for reference:
    # if day_est_pnl(db, session) <= DAY_LOSS_STOP:
    #     return f"day loss stop {DAY_LOSS_STOP:.0%}"
    return None


def last_entry(session: date) -> time:
    """Last tested entry minute: 15:44 ET, and never at or after the session's close. The research population
    (telegram_alert_test primary_window: entry bar 09:30-15:44, RTH bars only) holds half-day entries up to 12:59
    with the exit at the 13:00 close, so the live rule is the same (adversarial passes 4-5, P4-2/P5-2: a fixed 15:44
    let the paper book short after a 13:00 bell; 16 minutes before the close was stricter than the tested rule)."""
    close = default_calendar().bounds(session).rth_close.astimezone(ET)
    return min(LAST_ENTRY, (close - timedelta(minutes=1)).time())


def prior_alert(db: OpsStore, symbol: str, day: date) -> str | None:
    """F8: the latest session 1..REPEAT_DAYS calendar days before ``day`` on which the channel alerted ``symbol`` in a
    tested cell inside the entry window, else None. The research counted every such alert with an entry bar priced
    $1-50, whatever we did with it. Live counts the alerts we traded, those the short skipped for news, beta or F8, and
    operational skips (paused, late, not approved): real alerts whose price and entry bar were never checked, so the
    live set can be slightly larger than the tested one (review 2026-11-12: 4 of 100 live decisions, all OMH, a true
    multi-day runner). It never counts alerts shown not to be events: price/window filters and NOT_EVENT_SKIPS. A row
    that settlement later voids still counts until then (rare; same conservative direction)."""
    lo, hi = (day - timedelta(days=REPEAT_DAYS)).isoformat(), (day - timedelta(days=1)).isoformat()
    marks = ",".join("?" * len(NOT_EVENT_SKIPS))
    with db.tx() as c:
        r = c.execute("SELECT MAX(session) FROM fade_trades WHERE symbol=? AND session BETWEEN ? AND ? AND ("
                      "status IN ('ask','pending','open','est_closed','settled') "
                      f"OR (status='skipped' AND reason NOT IN ({marks})) "
                      f"OR (status='filtered' AND reason IN ({','.join('?' * len(EVENT_FILTERS))})))",
                      (symbol, lo, hi, *NOT_EVENT_SKIPS, *EVENT_FILTERS)).fetchone()
    return r[0] if r else None


def on_alert(db: OpsStore, a: LiveAlert, now: datetime | None = None) -> str:
    """Classify one parsed alert. Returns what happened (for logs); texts nothing (``monitor`` does).

    Handled exactly once (2026-11-22): the alert's durable identifier is written with its row, so a second delivery
    of the same alert (push and poll, a replay after a restart, a retry) changes nothing and says so. A recovered
    alert (``a.via == "replay"``) goes through the SAME rules with the real ``now``: recovery decides nothing about
    trading. Older than MAX_ENTRY_DELAY it is "alert received late", as any late alert always was."""
    now = now or utcnow()
    if a.posted_at.tzinfo is None:
        raise ValueError("naive posted_at")
    if a.recovered_at is not None and a.recovered_at.tzinfo is None:
        raise ValueError("naive recovered_at")
    with db.read() as c:
        done = alert_log.find(c, alert_log.key(a))
    if done is not None:
        return f"already handled ({done['status']})"
    if a.alert_type not in CELLS or (a.alert_type == "news" and a.has_news is not True):
        return _no_row(db, a, now, "not a cell")
    et = a.posted_at.astimezone(ET)
    session = et.date().isoformat()
    if not default_calendar().is_session(et.date()):
        return _no_row(db, a, now, "not a session")
    entry_et = entry_start(a.posted_at).astimezone(ET).time()     # the tested entry bar's minute (09:30-15:44)
    last = last_entry(et.date())
    def put(status: str, reason: str, what: str) -> str:
        return "already handled" if _insert(db, a, session, status, reason, now) == ALREADY else what

    if not FIRST_ENTRY <= entry_et <= last:
        return put("filtered", f"outside 09:30-{last:%H:%M}", "filtered: window")
    if NO_NEWS and a.alert_type == "news":                         # amendment F6 (owner 2026-11-09)
        return put("filtered", NEWS_REASON, "filtered: news")
    if NO_REPEAT and prior_alert(db, a.symbol, et.date()) is not None:     # amendment F8 (owner 2026-11-12)
        return put("filtered", REPEAT_REASON, "filtered: repeat")
    if now - a.posted_at > MAX_ENTRY_DELAY:
        return put("skipped", "alert received late", "skipped: late")
    if a.via == "replay" and not RECOVERED_SHORT_ENTRIES:
        return put("skipped", RECOVERED_ONLY, "skipped: recovered, record only")
    if db.paused():
        reason, status = "paused", "skipped"
    elif (reason := caps(db, session)) is not None:
        status = "skipped"
    elif approvals.armed(db, now):
        status, reason = "pending", ""
    elif ASK_FIRST and not is_off(db):                 # never text an approval request for a short while off
        status, reason = "ask", "awaiting approval"
    else:
        status, reason = "pending", VETO
    rid = _insert(db, a, session, status, reason, now)
    if rid == ALREADY:
        return "already handled"
    if rid is None:
        return "duplicate symbol-session"
    return f"skipped: {reason}" if status == "skipped" else f"{status} #{rid}"


SWEEP_EVERY = 10.0      # seconds: while entries are being worked on, open positions are re-checked at least this often


def monitor(db: OpsStore, send, quote: Quote, now: datetime | None = None, borrow: Borrow | None = None,
            context: Callable[[str], dict] | None = None, news: NewsCheck | None = None,
            beta: BetaCheck | None = None, up_since: datetime | None = None, on_sweep=None) -> None:
    """Advance live estimates: open -> est_closed (stop / 60 min) FIRST, then ask -> pending (approval) and
    pending -> open (first quote, price filter). ``borrow(symbol, now)`` stamps IBKR/Alpaca borrow facts once per
    event. ``up_since``: when this listener came up; an entry never uses a print older than it (``_floor``).

    Open positions first (operational risk review 2026-11-23, reproduced): the pass used to walk every row in id
    order on one thread, and a row waiting to enter makes up to four network calls a pass (news, beta, quote, the
    halt check: 5 s timeouts each) for up to five minutes when its stock is halted. The stop check of an open
    position later in the list waited behind all of them, and the next pass started only 10 s after that. Now the
    open positions are checked at the start of every pass and again whenever SWEEP_EVERY has gone by while entries
    are being worked on. ``on_sweep()`` is called at each such check (the listener records late ones)."""
    fixed, now = now, now or utcnow()

    def sweep() -> float:
        done = None
        if on_sweep is not None:
            try:
                done = on_sweep()                           # may hand back what to call when the check is over
            except Exception:  # noqa: BLE001 - a measurement; the check itself goes on
                log.exception("sweep hook failed")
        when = fixed or utcnow()
        try:
            with db.read() as c:                            # a read: it does not wait for a writer
                held = c.execute("SELECT * FROM fade_trades WHERE status='open' ORDER BY id").fetchall()
            for r in held:
                try:
                    _advance(db, send, quote, r, when, context, news, beta, need_borrow=borrow is not None,
                             up_since=up_since)
                except Exception:  # noqa: BLE001 - one bad row must not stall the book; logged with its id
                    log.exception("fade row %s failed", r["id"])
        finally:
            if callable(done):
                try:
                    done()
                except Exception:  # noqa: BLE001 - a measurement
                    log.exception("sweep hook failed")
        return monotonic()

    last = sweep()
    db.set_flag("fade_heartbeat", iso(now))
    with db.read() as c:
        rows = c.execute("SELECT * FROM fade_trades WHERE status IN ('ask','pending') ORDER BY id").fetchall()
    for r in rows:
        if monotonic() - last >= SWEEP_EVERY:
            last = sweep()
        try:
            if borrow is not None and r["ib_snap"] is None and r["alp_etb"] is None:
                r = _stamp(db, r, borrow(r["symbol"], now))
            _advance(db, send, quote, r, now, context, news, beta, need_borrow=borrow is not None,
                     up_since=up_since)
        except Exception:  # noqa: BLE001 - one bad row must not stall the book; logged with its id
            log.exception("fade row %s failed", r["id"])


def _stamp(db: OpsStore, r, facts: dict) -> dict:
    """Record borrow facts at alert time (plan 2026-11-04 W1). Only known fields are written."""
    cols = {k: v for k, v in facts.items() if k in BORROW_COLS and v is not None}
    if cols:
        keys = ", ".join(f"{k}=?" for k in cols)
        with db.tx() as c:
            c.execute(f"UPDATE fade_trades SET {keys} WHERE id=?", (*cols.values(), r["id"]))  # noqa: S608 - fixed names
    return {**dict(r), **cols}


def record(db: OpsStore) -> tuple[int, float | None]:
    """Settled live-book trades and their mean official net."""
    with db.tx() as c:
        n, mean = c.execute("SELECT COUNT(*), AVG(net) FROM fade_trades WHERE status='settled'").fetchone()
    return int(n), mean


def _advance(db: OpsStore, send, quote: Quote, r, now: datetime, context=None, news: NewsCheck | None = None,
             beta: BetaCheck | None = None, need_borrow: bool = False, up_since: datetime | None = None) -> None:
    """``need_borrow``: the book has a borrow source (the listener always does), so the borrow rule applies."""
    if r["status"] == "ask":
        _ask(db, send, quote, r, now, context, news, beta, need_borrow, up_since)
    elif r["status"] == "pending":
        _enter(db, send, quote, r, now, context, news, beta, need_borrow, up_since)
    else:
        _exit(db, send, quote, r, now)


def _approval(db: OpsStore, rid: int):
    with db.tx() as c:
        return c.execute("SELECT * FROM approvals WHERE strategy=? AND kind='fade' ORDER BY id DESC LIMIT 1",
                         (f"fade:{rid}",)).fetchone()


def _ask(db: OpsStore, send, quote: Quote, r, now: datetime, context=None, news: NewsCheck | None = None,
         beta: BetaCheck | None = None, need_borrow: bool = False, up_since: datetime | None = None) -> None:
    """Text one approval request per event; an approved request becomes a pending entry (same tick)."""
    posted = datetime.fromisoformat(r["posted_at"])
    deadline = posted + MAX_ENTRY_DELAY - ASK_MARGIN
    a = _approval(db, r["id"])
    if a is None and is_off(db):                      # review 2026-11-12: an ask row from before the switch never
        _set(db, r["id"], "ask", status="pending", reason=VETO)      # texts a request; _enter classifies it as off
        return
    if a is None:
        if now >= deadline:
            _set(db, r["id"], "ask", status="skipped", reason="no time left to ask")
            return
        px = quote(r["symbol"])
        if px is not None and px > 0 and not PRICE_MIN <= px <= PRICE_MAX:
            _set(db, r["id"], "ask", status="filtered", reason=f"price {_fmt(px)} outside $1-50")
            return
        ctx = _context(db, r, context)
        summary = ask_text(r["symbol"], r["alert_type"], px, entry_notional(db), ctx, borrow_note(r), record(db), at=now,
                           forward=forward_alarm(db))
        aid = approvals.request(db, send, strategy=f"fade:{r['id']}", kind="fade", period=r["session"],
                                summary=summary, payload={"fade_id": r["id"]}, expires_at=deadline, now=now)
        if aid is None:
            _set(db, r["id"], "ask", status="skipped", reason="approval not sent (paused or SMS failed)")
        return
    if a["status"] == "approved":
        if not approvals.claim(db, a["id"], now):               # expired between the reply and this tick
            _set(db, r["id"], "ask", status="skipped", reason="approval expired before entry")
            return
        if db.paused() or now > deadline + ASK_MARGIN:
            approvals.finish(db, a["id"], False, "paused or too late")
            _set(db, r["id"], "ask", status="skipped", reason="paused or too late after approval")
            return
        approvals.finish(db, a["id"], True, "entering")
        if _set(db, r["id"], "ask", status="pending", reason="approved"):  # the owner's OK: no at-entry gates
            # borrow is not a preference: an approved short still needs shares (2026-11-16)
            _enter(db, send, quote, {**dict(r), "status": "pending", "reason": "approved"}, now, context,
                   need_borrow=need_borrow, up_since=up_since)
        return
    if a["status"] == "rejected":
        _set(db, r["id"], "ask", status="skipped", reason="declined")
        return
    if a["status"] in ("failed", "superseded") or (a["status"] == "expired" and a["result"] in ("paused", "lockout")):
        _set(db, r["id"], "ask", status="skipped", reason=f"approval {a['result'] or a['status']}")
        return
    if now < deadline:
        return                                        # still waiting for a reply
    # Opt-out (owner decision 2026-11-04): silence at the deadline = enter, unless PAUSE / DISARM.
    if approvals.disarmed(db, now):
        _set(db, r["id"], "ask", status="skipped", reason="paused or disarmed before auto-entry")
        return
    if now > deadline + ASK_MARGIN:                    # listener was down past the tested entry window
        _set(db, r["id"], "ask", status="skipped", reason="auto-entry window missed")
        return
    with db.tx() as c:                                 # claim the silent request exactly once
        won = c.execute("UPDATE approvals SET status='executed', decided_at=?, result='auto-entered at expiry "
                        "(no reply)' WHERE id=? AND (status='pending' OR (status='expired' AND result IS NULL))",
                        (iso(now), a["id"])).rowcount
    if not won:                                        # someone else decided it (e.g. PAUSE): never enter
        _set(db, r["id"], "ask", status="skipped", reason="approval no longer open")
    elif _set(db, r["id"], "ask", status="pending", reason="auto"):
        _enter(db, send, quote, {**dict(r), "status": "pending", "reason": "auto"}, now, context, news, beta,
               need_borrow, up_since)


def _floor(r, start: datetime, up_since: datetime | None) -> datetime:
    """The earliest print a short entry may use: the tested entry bar's start and, never earlier, the moment this
    listener could act on the alert: when the alert was recovered (a replay) and when the process came up. Listener
    recovery 2026-11-22 (owner: "never use a historical price as an executable entry"): after a restart the latest
    print can be minutes old, from before the bot knew of the alert or was running. For an alert handled live both
    are before ``start``, so nothing changes."""
    rec = r["recovered_at"] if "recovered_at" in r.keys() else None
    marks = [t for t in (datetime.fromisoformat(rec) if rec else None, up_since) if t is not None]
    return max([start, *marks])


def _enter(db: OpsStore, send, quote: Quote, r, now: datetime, context=None, news: NewsCheck | None = None,
           beta: BetaCheck | None = None, need_borrow: bool = False, up_since: datetime | None = None) -> None:
    posted = datetime.fromisoformat(r["posted_at"])
    start = entry_start(posted)
    if now < start:
        return
    close = default_calendar().bounds(date.fromisoformat(r["session"])).rth_close
    if now >= close or start >= close:                # P4-2: never open on a print after the bell
        _set(db, r["id"], "pending", status="skipped", reason="session closed before entry")
        return
    allowed = approvals.armed(db, now) or (r["reason"] in ("approved", "auto", VETO) and not approvals.disarmed(db, now))
    if not allowed:                                   # DISARM / PAUSE after the alert cancels the entry
        _set(db, r["id"], "pending", status="skipped", reason="disarmed before entry")
        return
    if NO_NEWS and news is not None and r["news_own"] is None and _company_news(db, r, news(r["symbol"], now)):
        return
    no_beta = False
    if beta is not None and r["beta"] is None:
        b = beta(r["symbol"], now)
        if _high_beta(db, r, b):
            return
        no_beta = b is None                           # owner 2026-11-21: unknown beta is no short (BETA_UNKNOWN)
    aware, floor = getattr(quote, "since_aware", False), _floor(r, start, up_since)
    late = floor > start                            # recovered, or the listener restarted, inside the entry bar or after
    over = now - posted > MAX_ENTRY_DELAY
    if late and (over or not aware):
        # After a restart the entry window is never stretched: a row that waited through the 5 minutes while the
        # listener was down is not entered on the first print it sees afterwards (before 2026-11-22 it was, however
        # late). And a quote that cannot prove its time is never a restart entry.
        px = None
    else:
        px = quote(r["symbol"], since=floor) if aware else quote(r["symbol"])
    saw = (getattr(quote, "seen", None) or {}).get(r["symbol"]) or {}     # what the quote saw (a record, not a rule)
    if px is None or not px > 0:
        if "entry_wait" in r.keys() and r["entry_wait"] is None:
            # 2026-11-24: the FIRST pass without a price writes down why, once. What the live CODE does is unchanged:
            # it waits for a free-feed print at or after the tested bar, up to 5 minutes after the alert, then skips.
            # That is not the registered rule's wording (2026-11-25): the registration enters at ONE bar, the first
            # official minute at or after alert + 10 s, and its five minutes only bound how late that first bar may
            # be (recovery_verification_2026-11-23.md, section 1). A live entry AFTER that bar, because the free feed
            # had no print while the official tape did, is a later entry the registration does not define: the open
            # late-entry question (ops_risk_register_2026-11-23.md), recorded here, decided by the owner, not by code.
            why = (RESTART_WAIT if late and (over or not aware) else saw.get("why")) or NO_PRICE_YET
            try:
                with db.tx() as c:
                    c.execute("UPDATE fade_trades SET entry_wait=? WHERE id=? AND status='pending' AND entry_wait IS "
                              "NULL", (f"{iso(now)} {why}"[:300], r["id"]))
            except Exception:  # noqa: BLE001 - a record: failing to write it must never change what the entry does
                log.exception("entry wait of fade %s not recorded", r["id"])
        if over:                                    # the tested rule: no bar within 5 minutes -> no trade (halts)
            traded = late and (not aware or (quote(r["symbol"], since=start) or 0) > 0)   # it printed; we were away
            _set(db, r["id"], "pending", status="skipped", reason=RESTART_NO_PRINT if traded else NO_PRINT_REASON)
        return
    if not PRICE_MIN <= px <= PRICE_MAX:
        _set(db, r["id"], "pending", status="filtered", reason=f"price {_fmt(px)} outside $1-50")
        return
    if no_beta:                                       # read again on every pass of the entry window, then no short
        if now - posted > MAX_ENTRY_DELAY:
            _set(db, r["id"], "pending", status="filtered", reason=BETA_UNKNOWN)
        return
    if need_borrow and (why := no_shares(db, r, px)) is not None:   # owner 2026-11-16: no shares, no short (no text)
        _set(db, r["id"], "pending", status="filtered", reason=why)
        return
    if is_off(db):                                    # owner 2026-11-12: a would-be short, not taken (no text)
        _set(db, r["id"], "pending", status="filtered", reason=OFF_REASON)
        return
    when = {"est_print_at": saw["at"]} if saw.get("at") else {}      # the print the price came from (data age)
    elig = ELIG_REGISTERED
    if now >= start + ENTRY_MINUTE:                   # owner 2026-11-25: the registered entry minute has passed
        got = datetime.fromisoformat(r["created_at"]) if r["created_at"] else posted
        exempt = ("an approved entry (ask-first mode)" if r["reason"] in ("approved", "auto") else
                  "the restart rules decide it" if late else None)
        if exempt is None:
            what, evidence = _earlier_trading(quote, r["symbol"], start, now)
            # Owner 2026-11-25 (second decision): "Delivery-late short alerts are record-only under the same
            # registered-minute eligibility principle." An alert that itself reached the book after its registered
            # minute had ended is not shorted either; the record says that it arrived late, and how late.
            arrived = (f"the alert reached the book {got.astimezone(ET):%H:%M:%S} ET, "
                       f"{int((got - start).total_seconds())} s after its registered minute began; "
                       if got >= start + ENTRY_MINUTE else "")
            _set(db, r["id"], "pending", status="skipped", reason=SUBSTITUTE_REASON, entry_elig=what,
                 entry_evidence=(arrived + evidence + f"; the print not used: ${_fmt(px)}")[:400],
                 entry_rule=ENTRY_RULE, **when)
            return                                    # recorded, never shorted; no text (as every skip)
        elig = ELIG_EXEMPT + exempt
    if _set(db, r["id"], "pending", status="open", est_entry=px, est_last=px, est_entry_at=iso(now), entry_elig=elig,
            entry_rule=ENTRY_RULE, est_last_print_at=_print_at(saw, px), **when):
        out = _exit_at(r["session"], now)[0].astimezone(ET)
        if r["reason"] == VETO:
            _veto_text(db, send, r, px, now, out, context)
            return
        ctx = _context(db, r, context)
        with db.tx() as c:                            # the beta stamped by the F7 check, for the text (2026-11-10)
            ctx = {**ctx, "beta": c.execute("SELECT beta FROM fade_trades WHERE id=?", (r["id"],)).fetchone()[0]}
        how = " " + italic("(auto: no reply in time)") if r["reason"] == "auto" else ""
        send("[SGT] " + ask_text(r["symbol"], r["alert_type"], px, entry_notional(db), ctx, borrow_note(r), record(db),
                                 out_at=out, at=now, forward=forward_alarm(db)) + how)


def _earlier_trading(quote, symbol: str, start: datetime, now: datetime) -> tuple[str, str]:
    """(eligibility label, evidence) for a would-be entry after its registered minute. ``quote.traded_between`` (the
    live feed's own trade list, odd lots included) can CONFIRM that the stock traded between the start of the
    registered minute and the start of the current one. Nothing available in time can confirm that it did not: a
    feed without that list, a failed request and a silent feed all end as "unknown", never as "no trading"."""
    upto = now.replace(second=0, microsecond=0)
    span = f"{start.astimezone(ET):%H:%M:%S} to {upto.astimezone(ET):%H:%M:%S} ET"
    look = getattr(quote, "traded_between", None)
    if look is None:
        return ELIG_UNKNOWN, f"the live quote has no trade list to look at ({span})"
    try:
        state, detail = look(symbol, start, upto)
    except Exception as exc:  # noqa: BLE001 - evidence only: its failure is "unknown", never an entry
        log.warning("earlier-trading lookup for %s failed: %s", symbol, type(exc).__name__)
        return ELIG_UNKNOWN, f"the free feed's trade list could not be read ({type(exc).__name__}; {span})"
    if state == "yes":
        return ELIG_SUBSTITUTE, f"free feed: {detail}"
    if state == "no":
        return ELIG_UNKNOWN, (f"no free-feed trade from {span}; that is the free feed's silence only: the official tape "
                              "is 15 minutes behind, so a halt or an untraded minute is NOT confirmed")
    return ELIG_UNKNOWN, f"the free feed's trade list gave no answer ({detail}; {span})"


def _context(db: OpsStore, r, context) -> dict:
    """Tape + company news for the trade text (informational: a failure never blocks the text or the trade). The
    news flags and the confidence shown are stored on the trade for the OUT text and the forward check."""
    if context is None:
        return {}
    try:
        ctx = context(r["symbol"]) or {}
    except Exception:  # noqa: BLE001 - context is informational; the text still goes out
        log.warning("context %s failed", r["symbol"])
        ctx = {}
    n = ctx.get("news")
    cols = {"conf": confidence(r["alert_type"], ctx, datetime.fromisoformat(r["posted_at"]))[0]}   # v2, A2
    if n:
        cols.update(news_own=int(bool(n.get("news_own"))), news_earn=int(bool(n.get("news_earn"))),
                    news_head=n.get("headline"), news_kind=n.get("news_kind"))
    fl = ctx.get("filings")
    if fl:
        cols.update(filing_dilution=int(bool(fl.get("dilution_30d"))), filing_form=fl.get("dilution_form"),
                    filing_8k=int(bool(fl.get("eightk_today"))))
    st = ctx.get("social")
    if st:
        cols.update(st_n60=st.get("st_n60"), st_n15=st.get("st_n15"), st_span=st.get("st_span_min"),
                    st_bull=st.get("st_bull"), st_bear=st.get("st_bear"), st_watch=st.get("st_watch"))
    xs = ctx.get("x")
    if xs:
        cols.update({k: xs.get(k) for k in ("x_n60", "x_n15", "x_cat24")})
        if xs.get("x_cat_posts") is not None:
            cols["x_cat_posts"] = json.dumps(xs["x_cat_posts"])[:4000]
    rd = ctx.get("reddit")
    if rd:
        cols.update({k: rd.get(k) for k in ("rd_rank", "rd_mentions", "rd_upvotes", "rd_mentions_prev")})
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        c.execute(f"UPDATE fade_trades SET {keys} WHERE id=?", (*cols.values(), r["id"]))  # noqa: S608 - fixed names
    return ctx


def _veto_text(db: OpsStore, send, r, px: float, now: datetime, out: datetime, context) -> None:
    """F5 entry text: the full trade card + "NO <code> = exit now" (approval kind 'fadeexit', open until the exit)."""
    ctx = _context(db, r, context)
    body = ask_text(r["symbol"], r["alert_type"], px, entry_notional(db), ctx, borrow_note(r), record(db), out_at=out,
                    at=now, forward=forward_alarm(db))
    approvals.request(db, send, strategy=f"fade:{r['id']}", kind="fadeexit", period=r["session"], summary=body,
                      payload={"fade_id": r["id"], "symbol": r["symbol"]}, expires_at=out, now=now)


def _vetoed(db: OpsStore, rid: int) -> bool:
    with db.tx() as c:
        return c.execute("SELECT 1 FROM approvals WHERE strategy=? AND kind='fadeexit' AND status='rejected'",
                         (f"fade:{rid}",)).fetchone() is not None


def _exit_at(session: str, started: datetime) -> tuple[datetime, str]:
    """Time-exit of a live trade: 60 minutes, or the regular-session close if earlier (the research window holds
    only RTH bars, so a late entry is covered at the last RTH bar; plan W5 2026-11-04)."""
    close = default_calendar().bounds(date.fromisoformat(session)).rth_close
    return (started + HOLD, "60 min") if started + HOLD <= close else (close, "close")


SRC_UNNAMED = "the book's quote (reader not named)"
SRC_CARRIED = "carried from an earlier look"
AT_OBSERVED = "observed"
AT_NONE = "source time missing: the reader gave a price without a time"
AT_UNREADABLE = "source time missing: the time given is not a time with a zone"
AT_CARRIED_NONE = "source time missing: the carried price has no print time on record"
AT_UNBOUND = ("source time missing: the reader's record does not carry the price it returned, so its time cannot "
              "be tied to this reading")


def exit_source(now: datetime, src: str | None, px: float | None, at: object, carried: bool = False,
                why: str | None = None, unbound: bool = False) -> dict:
    """The columns that say where an exit ESTIMATE came from (owner 2026-11-27; a record, never a rule: nothing
    reads these to decide or to price). ``px`` is the source price and ``at`` the time of the event it belongs to.
    A time that is absent or not a zoned time is recorded as missing, in words, with the time and the age left
    NULL: it is never rebuilt from another reading. ``carried``: no price at the decision, so the book used its
    last mark; ``why`` is what the reader said about having no price then. ``unbound``: the reader's record could
    not be tied to the price it returned (see ``_bound``), so no time is taken from it. The age may be large (a
    quiet stock, or a carried mark) and is recorded as it is; it classifies nothing.

    What the record establishes: when that price occurred on that reader. It does not establish that an order
    could have been executed at it, then or at the decision."""
    when, note = None, AT_CARRIED_NONE if carried else AT_UNBOUND if unbound else AT_NONE
    if unbound:
        at = None
    if isinstance(at, datetime):
        at = at.isoformat()
    if isinstance(at, str) and at:
        try:
            t = datetime.fromisoformat(at)
            when, note = (t, AT_OBSERVED) if t.tzinfo is not None else (None, AT_UNREADABLE)
        except ValueError:
            note = AT_UNREADABLE
    name = src or SRC_UNNAMED
    if carried:
        name = f"{name}, {SRC_CARRIED}; at the decision: {why or 'no price'}"
    return {"exit_decided_at": iso(now), "exit_src": name[:300],
            "exit_src_px": float(px) if px is not None else None,
            "exit_src_at": when.isoformat() if when is not None else None,
            "exit_src_age_s": round((now - when).total_seconds(), 3) if when is not None else None,
            "exit_src_note": note}


def exit_source_safe(now: datetime, src: object, px: float | None, at: object, **kw) -> dict:
    """``exit_source``, never in the way of an exit: a record that cannot be built is logged and left empty (every
    new column NULL), and the exit goes ahead exactly as it would have."""
    try:
        return exit_source(now, src if isinstance(src, str) else None, px, at, **kw)
    except Exception:  # noqa: BLE001 - a record: failing to build it must never stop or change an exit
        log.exception("exit source not recorded")
        return {}


def _bound(saw: dict, px: float | None) -> bool:
    """Is ``saw`` the record of the very reading that returned ``px``? Only when it carries that price itself. The
    exit's source must be the exact observation used, never a second reading: a record without the price, or with
    another price, is not taken as this reading's, whatever else it says."""
    got = saw.get("px") if isinstance(saw, dict) else None
    return (px is not None and isinstance(got, (int, float)) and not isinstance(got, bool) and got == px)


def _print_at(saw: dict, px: float | None) -> str | None:
    """The print time of the reading that returned ``px``: only from a record bound to it, only if it is text."""
    at = saw.get("at") if _bound(saw, px) else None
    return at if isinstance(at, str) and at else None


def _exit(db: OpsStore, send, quote: Quote, r, now: datetime) -> None:
    px = quote(r["symbol"])
    saw = (getattr(quote, "seen", None) or {}).get(r["symbol"]) or {}     # what this look saw (a record, not a rule)
    saw = saw if isinstance(saw, dict) else {}
    entry, started = r["est_entry"], datetime.fromisoformat(r["est_entry_at"])
    fresh = px is not None and px > 0
    if fresh:
        # the mark's own print time beside it; None when this look gave none, so an older time never stays on a
        # newer price
        _set(db, r["id"], "open", est_last=px, est_last_print_at=_print_at(saw, px))
    last = px if fresh else r["est_last"]
    end, why_end = _exit_at(r["session"], started)
    vetoed = r["reason"] == VETO and _vetoed(db, r["id"])
    why = "stop" if last and last >= entry * (1 + STOP) else (VETO_EXIT if vetoed else (why_end if now >= end else None))
    if why is None:
        return
    exit_px = max(last, entry * (1 + STOP)) if why == "stop" else last
    src = getattr(quote, "source", None)
    held = r["est_last_print_at"] if "est_last_print_at" in r.keys() else None
    why_none = saw.get("why") if isinstance(saw.get("why"), str) else None
    loose = bool(saw.get("at")) and not _bound(saw, px)      # a time on record that nothing ties to this price
    source = (exit_source_safe(now, src, last, _print_at(saw, px), unbound=loose) if fresh else
              exit_source_safe(now, src, last, held, carried=True, why=why_none))
    if _set(db, r["id"], "open", status="est_closed", est_exit=exit_px, est_exit_at=iso(now), reason=why, **source):
        with db.tx() as c:                    # a NO after the exit is answered "already closed", never acted on
            c.execute("UPDATE approvals SET status='expired', decided_at=?, result='trade closed' WHERE strategy=? "
                      "AND kind='fadeexit' AND status='pending'", (iso(now), f"fade:{r['id']}"))
        cost = est_costs.cost("short", r["symbol"], r["est_entry_at"], entry) or 0.0
        g = (entry - exit_px) / entry - cost             # 2026-11-16: the texted result is after estimated costs
        v = nav(db)
        usd = g * (r["weight"] or WEIGHT) * v
        with db.tx() as c:
            n_day = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=? AND status='est_closed'",
                              (r["session"],)).fetchone()[0]
        send(out_text(r, entry, exit_px, why, usd, day_est_pnl(db, r["session"]) * v, n_day, cost=cost))


def _company_news(db: OpsStore, r, flags: dict | None) -> bool:
    """Amendment F6: stamp the entry-time news flags; True (and filtered) when the company has news today.
    Unknown (None) never blocks and is retried on the next tick only while the entry is still pending."""
    if flags is None:
        return False
    own, earn = int(bool(flags.get("news_own"))), int(bool(flags.get("news_earn")))
    if own or earn:
        _set(db, r["id"], "pending", status="filtered", reason=NEWS_REASON, news_own=own, news_earn=earn,
             news_head=flags.get("headline"), news_kind=flags.get("news_kind"))
        return True
    _set(db, r["id"], "pending", news_own=0, news_earn=0, news_kind=flags.get("news_kind"))
    return False


def _high_beta(db: OpsStore, r, b: float | None) -> bool:
    """Amendment F7: stamp the beta; True (and filtered) when it is >= HIGH_BETA_SKIP. Unknown (None) is not stamped:
    the caller reads it again on the next pass and takes no short while it stays unknown (owner 2026-11-21)."""
    if b is None:
        return False
    if b >= HIGH_BETA_SKIP:
        _set(db, r["id"], "pending", status="filtered", reason=BETA_REASON, beta=b)
        return True
    _set(db, r["id"], "pending", beta=b)
    return False


def _set(db: OpsStore, rid: int, expect: str, **cols) -> bool:
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        done = c.execute(f"UPDATE fade_trades SET {keys} WHERE id=? AND status=?",  # noqa: S608 - fixed column names
                         (*cols.values(), rid, expect)).rowcount == 1
    if done and any(k in cols for k in RECORDED):     # a price mark alone (est_last) is not a decision
        db.decide("short", rid, None, expect, cols.get("status", expect), cols.get("reason"), RULE_VERSION,
                  **{k: v for k, v in cols.items() if k not in ("status", "reason")})
    return done


def settled_exit(r) -> str | None:
    """How a SETTLED short's rule replay ended, read off its own stored numbers (2026-11-24). The row's ``reason``
    is the LIVE path's exit and stays that after the settle, so GLXG 11-24 read "60 min" beside a settled cover at
    the stop. "stop": the cover is at or above entry x 1.2 (the stop level, or the open of a minute that gapped over
    it); the owner's NO; otherwise the timer: "60 min", or "close" when the hour ran past the session's close.
    None = not settled."""
    r = dict(r)
    entry, cover = r.get("entry"), r.get("cover")
    if entry is None or cover is None or not entry > 0:
        return None
    if cover >= entry * (1 + STOP) - 1e-9:
        return "stop"
    if r.get("reason") == VETO_EXIT:
        return VETO_EXIT
    try:
        started = datetime.fromisoformat(str(r["entry_ts"]))
        started = started if started.tzinfo is not None else started.replace(tzinfo=timezone.utc)
        return _exit_at(str(r["session"]), started)[1]
    except (KeyError, ValueError):
        return "60 min"


def due(db: OpsStore, now: datetime | None = None) -> pd.DataFrame:
    """Trades whose official 60-minute window is final on SIP (``SETTLE_AFTER`` past the alert)."""
    now = now or utcnow()
    with db.tx() as c:
        rows = c.execute("SELECT f.*, CASE WHEN a.id IS NOT NULL THEN f.est_entry_at END AS entry_after "
                         "FROM fade_trades f LEFT JOIN approvals a ON a.strategy = 'fade:' || f.id AND a.kind='fade' "
                         "AND a.status='executed' WHERE f.status IN ('open','est_closed') AND f.est_entry_at <= ? "
                         "ORDER BY f.id", (iso(now - SETTLE_AFTER),)).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def settle(db: OpsStore, official: Official, now: datetime | None = None) -> list[str]:
    """Book official results. ``official(due_rows)`` returns per ``id``: qualifies (bool), entry_ts, entry, cover,
    cost_rt, gross, net (research code) and ``why``. A taken short is never cut (owner 2026-11-18): one that is not a
    tested event (e.g. official entry outside $1-50) comes back scored at its live entry, with ``why`` saying so
    (stored as the reason). Void only when ``official`` has no result at all: no trade was possible on the tape."""
    now = now or utcnow()
    d = due(db, now)
    if d.empty:
        return []
    res = official(d).set_index("id")
    lines = []
    for r in d.itertuples(index=False):
        if r.id not in res.index:
            continue                                         # bars not available yet: try next run
        o = res.loc[r.id]
        if not bool(o["qualifies"]) or pd.isna(o["net"]):
            _set(db, r.id, r.status, status="void", reason=str(o.get("why", "not a research event")), settled_at=iso(now))
            lines.append(f"{r.symbol} void")
            continue
        rule = {k: float(o[k]) for k in ("rule_entry", "rule_net") if k in o and pd.notna(o[k])}
        why = o.get("why")
        if isinstance(why, str) and why and getattr(r, "reason", None) != VETO_EXIT:
            rule["reason"] = why                             # how this result was made, when not the tested way
        # 2026-11-25 (option A): the settled figures are the registered arithmetic's and say so; the same replay
        # under price-decimal-v1 goes into its own columns when the scorer hands it over. Nothing reads those back.
        rule["calc"] = calc_v1.PRIMARY
        for k in calc_v1.SHORT_COLS:
            if k in o and pd.notna(o[k]):
                rule[k] = str(o[k]) if k == "calc_v1" else float(o[k])
        if _set(db, r.id, r.status, status="settled", entry_ts=str(o["entry_ts"]), entry=float(o["entry"]),
                cover=float(o["cover"]), cost_rt=float(o["cost_rt"]), gross=float(o["gross"]), net=float(o["net"]),
                settled_at=iso(now), **rule):
            lines.append(f"{r.symbol} {float(o['net']):+.2%}")
    return lines


def trade_usd(db: OpsStore) -> float | None:
    """The owner's dollar size per trade (flag ``SIZE_FLAG``), or None = the WEIGHT default. A value that is not a
    positive number is ignored with an error log: the default is the smaller, tested size (fail safe)."""
    raw = db.flag(SIZE_FLAG)
    if not raw:
        return None
    try:
        usd = float(raw)
    except ValueError:
        usd = float("nan")
    if not usd >= MIN_TRADE_USD or usd == float("inf"):
        log.error("fade size flag %r invalid: using the %.0f%% default", raw[:20], WEIGHT * 100)
        return None
    return usd


def trade_weight(db: OpsStore, v: float | None = None) -> float:
    """Share of the book for one new trade: the dollar setting / current NAV, capped at MAX_WEIGHT."""
    usd = trade_usd(db)
    if usd is None:
        return WEIGHT
    v = nav(db) if v is None else v
    return min(usd / v, MAX_WEIGHT) if v > 0 else 0.0


def notional(db: OpsStore) -> float:
    """The SIZE setting in dollars: trade_weight x current official book value (the SMS SIZE command compares to it)."""
    v = nav(db)
    return trade_weight(db, v) * v


BOOK = "alert fade (no-news)"                    # sgt.learning.live_monitor book name


def drift_scale(db: OpsStore) -> float:
    """The live monitor's size multiplier for new fades (1.0 unless a drift alarm halved it; 2026-11-12)."""
    from sgt.learning import live_monitor
    return live_monitor.scale(db, BOOK)


def forward_alarm(db: OpsStore) -> dict | None:
    """The forward record (every channel alert scored by the frozen rule) while its drift guard is in alarm, else None
    (audit 2026-11-21): the trade text then says that the tested win rate it quotes is not holding live. The site's
    money score already switches to this record (``sgt.ops.score``); the text did not. Never raises: a text must go out."""
    try:
        from sgt.learning import live_monitor

        r = live_monitor.check_forward(db)
        if r.get("alarms") and r.get("n") and r.get("mean") is not None:
            return {"n": int(r["n"]), "win": float(r["win"]), "mean": float(r["mean"])}
    except Exception:  # noqa: BLE001 - informational line; the text still goes out without it
        log.warning("forward record unreadable for the trade text")
    return None


def entry_notional(db: OpsStore) -> float:
    """Dollar size of one new fade trade as booked: the SIZE setting x the drift scale."""
    return notional(db) * drift_scale(db)


def nav(db: OpsStore, through: date | None = None) -> float:
    with db.tx() as c:
        rows = c.execute("SELECT weight, net FROM fade_trades WHERE status='settled'" +
                         (" AND session<=?" if through else "") + " ORDER BY settled_at, id",
                         ((through.isoformat(),) if through else ())).fetchall()
    v = NAV0
    for r in rows:
        v *= 1 + r["weight"] * r["net"]
    return v


CHANNEL_QUIET = 0.25                            # alerts today below this share of the usual day -> warn


def channel_health(db: OpsStore, session: str) -> list[str]:
    """Warn when the channel went quiet (self-fade study 2026-11-06: the edge is the channel's selection, so a silent
    or changed channel, or a listener that stopped receiving it, must show the same night). Needs 5+ past sessions."""
    with db.tx() as c:
        today = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=?", (session,)).fetchone()[0]
        past = [r[0] for r in c.execute("SELECT COUNT(*) FROM fade_trades WHERE session<? GROUP BY session "
                                        "ORDER BY session DESC LIMIT 20", (session,))]
    if len(past) < 5:
        return []
    usual = sorted(past)[len(past) // 2]
    if usual and today < CHANNEL_QUIET * usual:
        return [f"! channel quiet: {today} alerts today vs usual {usual} (listener or channel change?)"]
    return []


def digest_warnings(db: OpsStore, day: date, now: datetime | None = None, session: bool = True) -> list[str]:
    """Listener down / channel quiet: lines the owner must see first. ``session`` False (the exchange calendar says
    the market is closed on ``day``): no alerts is the expected count, so the channel is not called quiet (2026-11-26,
    Thanksgiving: "channel quiet: 0 alerts today vs usual 32" went out). The listener line is said on any day."""
    beat = db.flag("fade_heartbeat")
    down = beat and (now or utcnow()) - datetime.fromisoformat(beat) > HEARTBEAT_STALE
    warn = [f"! fade listener DOWN since {datetime.fromisoformat(beat).astimezone(ET):%m-%d %H:%M} ET"] if down else []
    return warn + (channel_health(db, day.isoformat()) if session else [])


def _record(db: OpsStore) -> tuple[int, float | None, tuple, list[tuple[str, int, float]]]:
    """(settled trades, mean net, (IBKR-borrowable n, mean), [(news label, n, mean)]) over the live record."""
    with db.tx() as c:
        n_all, mean = c.execute("SELECT COUNT(*), AVG(net) FROM fade_trades WHERE status='settled'").fetchone()
        ib = c.execute("SELECT COUNT(*), AVG(net) FROM fade_trades WHERE status='settled' AND ib_avail > 0").fetchone()
        # RAYA review 2026-11-06: news-type alerts or company news earned ~0 historically; owner kept trading them,
        # so the live record shows the split nightly for the n >= 300 decision. 2026-11-09: trades from before news
        # was recorded (news_own NULL, not a news alert) are "news unknown"; they used to overwrite the no-news group.
        nw = c.execute("SELECT CASE WHEN alert_type='news' OR news_own=1 THEN 'news' WHEN news_own=0 THEN 'no-news' "
                       "ELSE 'news unknown' END AS g, COUNT(*), AVG(net) FROM fade_trades WHERE status='settled' "
                       "GROUP BY g").fetchall()
    order = {"news": 0, "no-news": 1, "news unknown": 2}
    return n_all, mean, tuple(ib), sorted(((r[0], r[1], r[2]) for r in nw), key=lambda x: order[x[0]])


def record_line(db: OpsStore) -> str | None:
    """Digest RECORD line: the live fade record, its book, and the splits that decide the n >= 300 review."""
    n_all, mean, ib, groups = _record(db)
    if not n_all:
        return None
    v = nav(db)
    line = f"fade {n_all} trade{'s' * (n_all != 1)}, avg {mean:+.2%}/trade, book ${v:,.0f} ({v / NAV0 - 1:+.2%} since start)"
    line += "; " + ", ".join(f"{g} {n} avg {m:+.2%}" for g, n, m in groups)
    return line + f"; IBKR-borrowable {ib[0]}" + (f" avg {ib[1]:+.2%}" if ib[0] else "")


def digest_lines(db: OpsStore, day: date, now: datetime | None = None) -> list[str]:
    s = day.isoformat()
    warn = digest_warnings(db, day, now)
    with db.tx() as c:
        rows = c.execute("SELECT status, net FROM fade_trades WHERE session=?", (s,)).fetchall()
    n_all, _, ib, groups = _record(db)
    if not rows and not n_all:
        return warn
    st = [r for r in rows if r["status"] == "settled"]
    sk = sum(r["status"] == "skipped" for r in rows)
    wait = sum(r["status"] in ("pending", "open", "est_closed") for r in rows)
    day_net = sum(r["net"] for r in st) / len(st) if st else None
    v = nav(db)
    line = f"fade: ${v:,.0f} ({v / NAV0 - 1:+.2%} since start, {n_all} trades)"
    line += f"; today {len(st)} settled" + (f" avg {day_net:+.2%}/trade" if st else "")
    line += (f", {sk} skipped" if sk else "") + (f", {wait} unsettled" if wait else "")
    if n_all:                                 # the subset that could really be shorted (IBKR had shares)
        line += f"; IBKR-borrowable {ib[0]} trades" + (f" avg {ib[1]:+.2%}" if ib[0] else "")
        line += "; " + ", ".join(f"{g} {n} avg {m:+.2%}" for g, n, m in groups)
    return [line] + warn
