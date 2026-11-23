"""Listener recovery (owner 2026-11-22: "Build listener restart recovery now"): an alert posted while the listener
was down is found, recorded and labelled when it comes back, and is never traded on an old price.

How it works. There is ONE ordered path through the channel: ``ordered_pass`` asks for every message after the durable
cursor (``sgt.ops.alert_log``), oldest first, hands each to the book and moves the cursor only when every alert of
the message is on record. At start-up that pass IS the replay; while running it is the 15-second check that used to
be ``chan_poll.poll_once``. The push path stays as the fast path, but only once the ordered pass has reached the
newest message: until then a pushed message waits for the ordered pass, so nothing is handled out of channel order
(a later alert of a stock must never be handled before an earlier, still unprocessed one: the earlier one decides
whether the later is a duplicate or a repeat).

Recovery is NOT trade eligibility. A recovered alert (posted before this listener came up) is labelled
``via="replay"`` with its recovery time and then goes through ``fade.on_alert`` like any other alert, with the real
clock. The existing rules decide: older than five minutes it is "alert received late", inside the five minutes it
must still pass every gate with the facts of that moment, and its entry needs a print AFTER the recovery
(``fade._floor``); the long book counts a fill that began before the recovery as ``missed`` (``long_book.live_from``).
Nothing here adds a way to enter.

Bounded, and never silent: at most MAX_PER_PASS messages a pass; a message older than HORIZON is not handled and the
cursor passes it only with a row in ``alert_gaps``; an unreadable message or an alert that still fails after TRIES
passes is given up on the same way; a failing fetch leaves the cursor where it is and, at start-up, keeps the push
path waiting. The nightly summary's ``alerts:`` clause (``clause``) reports recovered alerts, gaps and an
unfinished catch-up. Design and what it does not cover: docs/findings/listener_recovery_2026-11-22.md.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sgt.fast import chan_poll, fade
from sgt.ops import alert_log
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
PAGE = 100                                  # messages per request (Telegram's own page size)
MAX_PER_PASS = 200                          # the bound of one pass; a longer backlog takes more passes, in order
HORIZON = timedelta(days=fade.REPEAT_DAYS + 1)   # an older alert can change no rule (F8 looks back 7 days): a gap
TRIES = 3                                   # passes an alert may fail before it is given up on (and written down)
OFF_EVERY = 300                             # seconds between ordered passes outside the session's alert hours
BEHIND_EVERY = 2                            # ... and while the catch-up has not reached the newest message
STATE_FLAG = "alert_catchup"                # the last known state of the catch-up, for the nightly summary
BEHIND_WARN = timedelta(minutes=10)         # a catch-up not complete for this long is a warning; a blip is not
TOO_OLD = "older than {d} days when the listener came back: it can change no rule, not handled"
UNREADABLE = "the message could not be read as alerts"
GAVE_UP = "an alert of the message could not be handled after {n} tries"
BAD_CURSOR = "the cursor could not be read: restarted from the newest message"


@dataclass
class State:
    channel: str
    up_since: datetime                      # when this listener began listening: anything posted before is a replay
    caught_up: bool = False                 # the ordered pass has reached the newest message (push may dispatch)
    kick: bool = False                      # a push arrived while not caught up: run the ordered pass again
    seen: chan_poll.Seen = field(default_factory=chan_poll.Seen)
    tries: dict[int, int] = field(default_factory=dict)
    told: str | None = None                 # the state last written to STATE_FLAG (written on change only)


@dataclass
class Pass:
    handled: int = 0                        # messages that went through the book
    recovered: int = 0                      # of them, posted before the listener was up
    skipped: int = 0                        # cursor moves past unhandled messages (each has a row in alert_gaps)
    done: bool = False                      # the newest message was reached
    stuck: int | None = None                # the message the pass stopped at (an alert of it is not on record yet)


def _posted(msg) -> datetime | None:
    try:
        return msg.date.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _audit(db: OpsStore, event: str, **detail) -> None:
    try:
        db.audit(event, **detail)
    except Exception:  # noqa: BLE001 - a note for the audit log must never cost the alert
        log.exception("audit %s not written", event)


def dispatch(db: OpsStore, msg, how: str, to_alerts, on_alert=None, now=utcnow) -> list[alert_log.Key] | None:
    """Every alert of one message to the book, each on its own (a failure is logged and audited, the next alert is
    still handled). Returns the identifiers of ALL the message's alerts, handled or not (``alert_log.advance`` checks
    which are on record), or None when the message cannot be read as alerts. ``how``: "push" | "poll" | "replay"."""
    on_alert = on_alert or fade.on_alert
    try:
        posted = msg.date.astimezone(timezone.utc)
        alerts = to_alerts(msg.message or "", posted, msg.id)
    except Exception:  # noqa: BLE001 - logged and audited; the listener goes on
        log.exception("message %s could not be read as alerts", getattr(msg, "id", "?"))
        _audit(db, "alert_failed", message_id=getattr(msg, "id", None), stage="parse", how=how)
        return None
    t, keys = now(), []
    for a in alerts:
        a = dataclasses.replace(a, via=how, recovered_at=t if how == "replay" else None)
        keys.append(alert_log.key(a))
        try:
            did = on_alert(db, a, t)
        except Exception:  # noqa: BLE001 - logged and audited; the next alert is still handled
            log.exception("alert %s %s (message %s) failed", a.symbol, a.alert_type, msg.id)
            _audit(db, "alert_failed", message_id=msg.id, symbol=str(a.symbol)[:12], stage="book", how=how)
            continue
        log.info("alert %s %s -> %s (%s, %.0f s after posting)", a.symbol, a.alert_type, did, how,
                 (t - posted).total_seconds())
    return keys


def pushed(state: State, handle):
    """The handler of the PUSH path: ``take(message)`` hands a message to ``handle(message, "push")`` once, and only
    when the ordered pass has caught up. Before that it only asks for another ordered pass, which will reach the
    message in its place in the channel's order. Returns whether it was dispatched here."""
    def take(message) -> bool:
        if not state.caught_up:
            state.kick = True
            return False
        if not state.seen.add(message.id):
            return False
        handle(message, "push")
        return True
    return take


async def ordered_pass(db: OpsStore, state: State, fetch, handle, newest, before, now=utcnow) -> Pass:
    """One pass from the durable cursor towards the newest message, oldest first.

    ``fetch(after, limit)`` -> messages with id > after (the oldest ``limit`` of them); ``handle(msg, how)`` ->
    ``dispatch``'s result; ``newest()`` -> the newest message's id; ``before(t)`` -> the id of the newest message
    posted before ``t`` (None when there is none). The cursor moves one message at a time and only when
    ``alert_log.advance`` finds every alert of the message on record; a failure of the channel or the database
    raises out of here with the cursor wherever it last was, and the next pass starts from it."""
    out, ch = Pass(), state.channel
    cur = alert_log.cursor(db, ch)
    if cur is None:                                         # first start with a cursor: nothing older is replayed
        top = await newest()
        if not isinstance(top, int) or top <= 0:
            out.done = True                                 # an empty channel: nothing to anchor on yet
            return out
        if db.flag(alert_log.CURSOR + ch) is not None:      # a value was there and cannot be used: say so
            alert_log.skip(db, ch, None, top, BAD_CURSOR)
        else:
            alert_log.baseline(db, ch, top)
        _audit(db, "alert_cursor_baseline", channel=ch, message_id=top)
        out.done = True
        return out
    while out.handled + out.skipped < MAX_PER_PASS:
        want = min(PAGE, MAX_PER_PASS - out.handled - out.skipped)
        got = list(await fetch(cur, want))
        msgs = sorted((m for m in got if isinstance(getattr(m, "id", None), int) and m.id > cur),
                      key=lambda m: m.id)[:want]
        if not msgs:
            out.done = True
            break
        t = now()
        first = _posted(msgs[0])
        if first is not None and t - first > HORIZON:       # a backlog too old to matter: one recorded jump
            edge = await before(t - HORIZON)
            if isinstance(edge, int) and edge > cur:
                alert_log.skip(db, ch, cur, edge, TOO_OLD.format(d=HORIZON.days), first_posted=iso(first))
                _audit(db, "alert_gap", channel=ch, from_id=cur, to_id=edge, why="horizon")
                cur, out.skipped = edge, out.skipped + 1
                continue
        for m in msgs:
            how = "replay" if (_posted(m) or t) < state.up_since else "poll"
            keys = handle(m, how)
            if keys is None:                                # unreadable: nothing to retry, written down, passed
                alert_log.skip(db, ch, cur, m.id, UNREADABLE, n=1)
                _audit(db, "alert_gap", channel=ch, from_id=cur, to_id=m.id, why="unreadable")
                out.skipped += 1
            elif alert_log.advance(db, ch, m.id, keys):
                out.handled, out.recovered = out.handled + 1, out.recovered + (how == "replay")
            else:                                           # an alert of the message is not on record
                n = state.tries[m.id] = state.tries.get(m.id, 0) + 1
                if n < TRIES:
                    out.stuck = m.id
                    return out                              # the cursor stays; the next pass tries again, in order
                lost = [(k, _posted(m) or t, t, how, t if how == "replay" else None) for k in keys]
                bad = alert_log.skip(db, ch, cur, m.id, GAVE_UP.format(n=TRIES), n=1, failed=lost)   # one transaction
                _audit(db, "alert_gap", channel=ch, from_id=cur, to_id=m.id, why="failed", symbols=bad)
                out.skipped += 1
            state.tries.pop(m.id, None)
            state.seen.add(m.id)
            cur = m.id
            await asyncio.sleep(0)                          # a long backlog must not starve the other loops
        if len(got) < want:                                 # a short page: that was the channel's newest message
            out.done = True
            break
    return out


async def catch_up(db: OpsStore, state: State, fetch, handle, newest, before, now=utcnow) -> Pass:
    """Ordered passes until the newest message is reached or one stops (its bound, a failing alert). Sets
    ``state.caught_up`` and keeps the durable note of the catch-up's state current. A push that arrived meanwhile
    (``state.kick``) gets one more pass before the push path is opened."""
    total = Pass()
    try:
        while True:
            state.kick = False
            out = await ordered_pass(db, state, fetch, handle, newest, before, now)
            total.handled, total.recovered = total.handled + out.handled, total.recovered + out.recovered
            total.skipped, total.done, total.stuck = total.skipped + out.skipped, out.done, out.stuck
            if not (out.done and state.kick):
                break
    except Exception as exc:
        _tell(db, state, False, error=type(exc).__name__)   # the caller logs and retries; the cursor is unchanged
        raise
    state.caught_up = total.done
    _tell(db, state, total.done, stuck=total.stuck)
    if total.recovered or total.skipped:
        _audit(db, "alert_replay", channel=state.channel, handled=total.handled, recovered=total.recovered,
               skipped=total.skipped, done=total.done)
        log.warning("alert catch-up: %d messages handled (%d posted while the listener was down), %d passed "
                    "unhandled and written down, %s", total.handled, total.recovered, total.skipped,
                    "complete" if total.done else "NOT complete")
    return total


def _tell(db: OpsStore, state: State, ok: bool, **why) -> None:
    """Keep STATE_FLAG current (written only when the state changes; never raises)."""
    what = json.dumps({"ok": ok, **{k: v for k, v in why.items() if v is not None}}, sort_keys=True)
    if what == state.told:
        return
    try:
        db.set_flag(STATE_FLAG, json.dumps({**json.loads(what), "at": utcnow().isoformat()}))
        state.told = what
    except Exception:  # noqa: BLE001 - a status note; the pass itself already succeeded or failed on its own
        log.exception("catch-up state not written")


def wait_s(state: State, every: int, in_hours: bool) -> int:
    """Seconds until the next ordered pass: at once-ish while behind, the fast check during alert hours (flag
    ``chan_poll_s``; 0 = no fast check), a slow one otherwise so the cursor keeps up with out-of-hours posts."""
    if not state.caught_up:
        return BEHIND_EVERY
    return every if every and in_hours else OFF_EVERY


# ---- accounting and the nightly clause -------------------------------------------------------------------------

ENTERED = ("open", "est_closed", "settled", "void")
LONG_BOUGHT = ("open", "est_closed", "settled")


def account(db: OpsStore, since: str, until: str | None = None, ledger: Path | None = None) -> dict:
    """What became of the alerts recovered after a restart, sessions ``since`` .. ``until`` (ISO dates, inclusive).

    The forward cohort's definition does not change: a recovered alert is an event like any other and its trade, if
    the rules allowed one, is a trade of the book. This keeps the stages apart so a result can be read with and
    without them: recovered (found by the catch-up) -> eligible (passed the rules that apply on receipt: a row
    waiting to enter) -> attempted (the paper book entered, at a print after the recovery) -> simulated-filled
    (the nightly execution model priced that trade with no exclusion flag). ``missed`` (never reached the book) is
    not here: the decision-record check counts it against the channel's own record."""
    hi = (until or "9999") + "￿"
    with db.read() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT e.symbol, e.status first, e.reason first_reason, e.fade_id, f.status, f.reason, f.est_entry, "
            "f.session, l.status long_status FROM alert_events e LEFT JOIN fade_trades f ON f.id=e.fade_id "
            "LEFT JOIN long_trades l ON l.fade_id=e.fade_id WHERE e.via='replay' AND e.status != ? AND "
            "e.posted_at >= ? AND e.posted_at < ? ORDER BY e.posted_at LIMIT 5000", (alert_log.NONE, since, hi))]
    ids = {r["fade_id"] for r in rows if r["est_entry"] is not None}
    priced = 0
    if ids:
        from sgt.fast import exec_book

        priced = sum(1 for r in exec_book.rows(ledger or exec_book.LEDGER)
                     if r["book"] == "short" and r["id"] in ids and r.get("label") == "MODELED")
    late = sum(1 for r in rows if r["first_reason"] == "alert received late")
    return {"recovered": len(rows), "eligible": sum(1 for r in rows if r["first"] in ("pending", "ask")),
            "attempted": len(ids), "simulated_filled": priced, "too_old": late,
            "failed": sum(1 for r in rows if r["first"] == alert_log.FAILED),
            "long_watched": sum(1 for r in rows if r["long_status"] is not None),
            "long_bought": sum(1 for r in rows if r["long_status"] in LONG_BOUGHT),
            "long_missed": sum(1 for r in rows if r["long_status"] == "missed"),
            "symbols": [r["symbol"] for r in rows][:10]}


def clause(db: OpsStore, session: str, cohort_from: str | None = None, now: datetime | None = None) -> str:
    """The ``alerts:`` clause of the nightly health line: always present; a warning when the catch-up has not been
    complete for BEHIND_WARN (one failed check that the next one repairs is not news), when the cursor passed
    messages it did not handle, or when an alert was given up on. Never raises."""
    try:
        a = account(db, session, session)
        gaps = alert_log.gaps(db, session)
        try:
            st = json.loads(db.flag(STATE_FLAG) or "{}")
        except ValueError:
            st = {}
        bad = []
        if st and not st.get("ok", True):
            try:
                since = datetime.fromisoformat(st["at"])
            except (KeyError, TypeError, ValueError):
                since = None
            if since is None or (now or utcnow()) - since >= BEHIND_WARN:
                what = f"stopped at message {st['stuck']}" if st.get("stuck") else st.get("error") or "not finished"
                when = f" since {since:%H:%M} UTC" if since else ""
                bad.append(f"catch-up NOT complete{when} ({what})")
        if gaps:
            ids = ", ".join(f"{g['from_id']}..{g['to_id']}" for g in gaps[:3]) + ("…" if len(gaps) > 3 else "")
            bad.append(f"{len(gaps)} stretch{'es' if len(gaps) != 1 else ''} of the channel NOT handled "
                       f"(message ids {ids}: {gaps[0]['reason']})")
        lost = alert_log.failed(db, session, session + "\uffff")   # however they arrived, not only replays
        if lost:
            bad.append(f"{lost} alerts failed")
        if a["recovered"]:
            rec = (f"{a['recovered']} recovered after a restart ({a['eligible']} eligible, {a['attempted']} entered on "
                   f"paper, {a['too_old']} too old to trade; longs {a['long_bought']} bought, {a['long_missed']} "
                   f"missed)")
        else:
            rec = "none recovered"
        out = ("⚠️ alerts: " + "; ".join(bad) + "; " + rec) if bad else f"alerts: in order, {rec}, no gap"
        if cohort_from and cohort_from <= session:
            t = account(db, cohort_from, session)
            if t["recovered"] > a["recovered"]:
                out += (f" (since {cohort_from[5:]}: {t['recovered']} recovered, {t['eligible']} eligible, "
                        f"{t['attempted']} entered, {t['simulated_filled']} priced by the model)")
        return out
    except Exception as e:  # noqa: BLE001 - the summary must go out; a broken check is itself the finding
        log.exception("alerts clause failed")
        return f"⚠️ alerts: check failed ({type(e).__name__})"
