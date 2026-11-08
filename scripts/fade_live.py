"""Live alert-fade book on the runner (2026-11-03; rule: docs/findings/prereg_alert_fade_2026-11-01.md).

    python scripts/fade_live.py login     # on the Mac, once: new Telegram session -> keychain sgt/tg_fade_session
    python scripts/fade_live.py push-secret   # keychain -> Fly secret over stdin (never printed)
    python scripts/fade_live.py run       # Fly: listen to the alert channel, enter/exit estimates, texts
    python scripts/fade_live.py settle    # Fly cron 20:40 ET: official results from SIP bars (research code)
    python scripts/fade_live.py borrow    # Fly cron every 15 min in market hours: IBKR borrow snapshot

The listener uses the owner's existing Telegram account (user decision 2026-11-04, replacing a dedicated account)
through its OWN session (a separate authorized device, revocable in Telegram > Settings > Devices without touching
the laptop session). The session string lives only in the keychain and the Fly secret SGT_SECRET_TG_FADE_SESSION,
and the listener subscribes to the alert channel only. Entries need ARM for the day (SMS), see
sgt.fast.fade for gates and caps. Official results reuse telegram_alert_test.attach_trades/primary_events and
alert_fade_test.score, so the book is scored exactly like the held-out test.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from alert_fade_test import LOCATE, cover, score
from backfill_alpaca import backfill
from sgt.core.secrets import get_secret
from sgt.crowd.telegram_parser import ParsedAlert, ParsedNews, parse_message
from sgt.crowd.telegram_collector import SourceMessage, TelegramCollector
from sgt.fast import borrow as borrow_mod
from sgt.fast import fade, filings, news, signals, social, xsocial
from sgt.intraday.sources import AlpacaHistoricalBars
from sgt.intraday.store import ParquetBarStore
from sgt.ops.sms import Sms
from sgt.ops.store import OpsStore, utcnow
from sgt.pilot import fade_real
from sgt.pilot.runtime import PilotHandle
from telegram_alert_test import attach_trades, primary_events

log = logging.getLogger("fade_live")
ET = ZoneInfo("America/New_York")
CHANNEL = -1001254273411                       # MetaTradings alerts (same as alert_fade_forward.CHANNEL)
SESSION_SECRET = "tg_fade_session"
DEVICE = "SGT fade listener (Fly)"              # how the session shows in Telegram > Settings > Devices
BARS = Path("data/fast")                       # own store: never mixes with research bar stores
TICK = 10                                      # seconds between monitor passes (approval -> entry latency)
POLL_EVERY = 3600                              # seconds between gap-filling channel polls (research record)
FORWARD_START = datetime(2026, 10, 30, tzinfo=timezone.utc)   # alert_fade_forward.FORWARD_START (amendment F1)
MAX_FADE_TEXTS_DAY = 40
MAX_PILOT_TEXTS_DAY = 150                       # real-money texts: own counter, never crowded out by paper
VOID_AFTER = timedelta(days=3)                 # SIP bars still missing after this -> void
STALE_QUOTE = timedelta(seconds=90)            # older IEX print + a large move = likely halted (RAYA 2026-11-06)
HALT_MOVE = 0.10                               # high/low range in the 5 min before the last print that looks like LULD
_SYMBOL = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def halted_like(client: httpx.Client, symbol: str, last_at: datetime) -> bool:
    """A stale IEX print after a violent move (>= HALT_MOVE in the 5 minutes before it) looks like a limit-up/down
    halt; a stale print without one is a stock that is just quiet on IEX (SNT 2026-11-06 traded every minute on SIP
    while IEX was silent). Unknown (no bars) counts as halted: waiting is the safe side."""
    b = live_bars(client, symbol, last_at + timedelta(minutes=1))
    if b.empty:
        return True
    w = b[b.ts >= last_at - timedelta(minutes=5)]
    if w.empty or w.low.min() <= 0:
        return True
    return bool(w.high.max() / w.low.min() - 1 >= HALT_MOVE)


def iex_quote(client: httpx.Client, now=lambda: datetime.now(timezone.utc)):
    """Latest IEX trade price, or None when that price cannot be used. Review 2026-11-06: RAYA's alert came during a
    LULD halt and the book 'entered' at the last pre-halt print ($1.425; reopen $1.52), so a print older than
    STALE_QUOTE after a violent move is not a price (entry waits for real trading, like the research rule). But SNT the
    same day was only quiet on IEX while trading every minute on SIP; skipping it diverged from the research rule, so a
    stale print WITHOUT such a move is still used (thin on IEX, not halted)."""
    def quote(symbol: str) -> float | None:
        if not _SYMBOL.match(symbol):
            return None
        try:
            r = client.get(f"/v2/stocks/{symbol}/trades/latest", params={"feed": "iex"})
            if r.status_code != 200:
                return None
            t = r.json().get("trade", {})
            p = float(t.get("p", 0))
            at = pd.Timestamp(str(t.get("t", ""))).to_pydatetime(warn=False)   # nanosecond ISO from Alpaca
            if at.tzinfo is None:
                return None
            if now() - at > STALE_QUOTE and halted_like(client, symbol, at):
                log.info("quote %s stale (%s) after a large move: likely halted, no price yet", symbol, at.isoformat())
                return None
            return p if p > 0 else None
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning("quote %s failed: %s", symbol, type(exc).__name__)
            return None
    return quote


class LiveClientSource:
    """MessageSource over the listener's own connected client (plan 2026-11-04 W2): the research record is
    filled on the runner without a second Telegram login (one auth key, one process)."""

    def __init__(self, client) -> None:
        self.client = client

    async def iter_messages(self, channel: str, *, min_id: int, limit: int, since: datetime | None = None):
        kwargs = {"reverse": True, "limit": limit, "min_id": min_id}
        if since is not None and min_id == 0:
            kwargs["offset_date"] = since
        async for msg in self.client.iter_messages(int(channel), **kwargs):
            if msg is not None:
                yield to_source(msg)


def to_source(msg) -> SourceMessage:
    return SourceMessage(id=int(msg.id), date=msg.date.astimezone(timezone.utc), text=msg.message or "",
                         edited_at=msg.edit_date.astimezone(timezone.utc) if msg.edit_date else None)


def asset_client() -> httpx.Client:
    from sgt.config import get_settings

    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id"), "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key")}
    return httpx.Client(base_url=get_settings().alpaca_paper_url.rstrip("/"), headers=h, timeout=5)


def alpaca_asset(client: httpx.Client):
    def get(symbol: str) -> dict:
        if not _SYMBOL.match(symbol):
            return {}
        r = client.get(f"/v2/assets/{symbol}")
        r.raise_for_status()
        j = r.json()
        return j if isinstance(j, dict) else {}
    return get


def iex_context(client: httpx.Client):
    """Tape for the request text from the IEX snapshot (IEX trades only, so approximate): % vs prior close and
    today's open, distance below today's high, today's VWAP, and today's volume so far vs all of yesterday's."""
    def ctx(symbol: str) -> dict:
        if not _SYMBOL.match(symbol):
            return {}
        r = client.get(f"/v2/stocks/{symbol}/snapshot", params={"feed": "iex"})
        if r.status_code != 200:
            return {}
        j = r.json() if isinstance(r.json(), dict) else {}
        day, prev = j.get("dailyBar") or {}, j.get("prevDailyBar") or {}
        px = float((j.get("latestTrade") or {}).get("p") or 0)
        o, h, vw, v = (float(day.get(k) or 0) for k in ("o", "h", "vw", "v"))
        pc, pv = float(prev.get("c") or 0), float(prev.get("v") or 0)
        out = {}
        if px > 0 and pc > 0:
            out["chg_day"] = px / pc - 1
        if px > 0 and o > 0:
            out["chg_open"] = px / o - 1
        if px > 0 and h >= px:
            out["off_high"] = px / h - 1
        if vw > 0:
            out["vwap"] = vw
        if v > 0 and pv > 0:
            out["rvol"] = v / pv
        now = datetime.now(timezone.utc)
        bars = live_bars(client, symbol, now)
        if not bars.empty:
            out["signals"] = signals.snapshot(bars, pd.Timestamp(now), prev_close=pc or None, prior_day_volume=pv or None)
        out["news"] = live_news(client, symbol, now)
        out["filings"] = filings.lookup(symbol, now, since_8k=news.window_start(now))   # None = unknown
        out["social"] = social.lookup(symbol, now)                                       # None = unknown
        out["x"] = xsocial.lookup(symbol, now)                    # None = dormant (no key), capped or unknown
        return out
    return ctx


def live_bars(client: httpx.Client, symbol: str, at: datetime) -> pd.DataFrame:
    """Today's IEX 1-minute bars up to ``at`` for the chart read in the text; empty on any failure."""
    start = datetime.combine(at.astimezone(ET).date(), datetime.min.time(), ET).replace(hour=4)
    try:
        r = client.get(f"/v2/stocks/{symbol}/bars", params={"timeframe": "1Min", "feed": "iex", "limit": 1000,
                                                             "start": start.isoformat(), "end": at.isoformat()})
        j = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return pd.DataFrame()
    rows = j.get("bars") if isinstance(j, dict) else None
    if not isinstance(rows, list):
        return pd.DataFrame()
    ok = [x for x in rows[:1000] if isinstance(x, dict) and all(isinstance(x.get(k), (int, float)) for k in "ohlcv")]
    if not ok:
        return pd.DataFrame()
    f = pd.DataFrame({"ts": pd.to_datetime([x.get("t") for x in ok], utc=True, errors="coerce"),
                      **{n: [float(x[k]) for x in ok] for k, n in zip("ohlcv", ("open", "high", "low", "close", "volume"))}})
    return f.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)


def live_news(client: httpx.Client, symbol: str, at: datetime) -> dict | None:
    """Company news before ``at`` from Alpaca's Benzinga feed, labelled exactly as prereg_fade_news measured it.
    None = unknown (API down); the text then says so instead of implying 'no news'."""
    try:
        r = client.get("/v1beta1/news", params={"symbols": symbol, "start": news.window_start(at).isoformat(),
                                                "end": at.isoformat(), "limit": 50, "sort": "desc"})
        items = r.json().get("news") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(items, list):
        return None
    rows = [{"created_at": a.get("created_at"), "symbols": ",".join(str(x) for x in (a.get("symbols") or [])[:20]),
             "headline": news.clean(a.get("headline"))} for a in items[:50] if isinstance(a, dict)]
    return news.flags(symbol, at, pd.DataFrame(rows, columns=["created_at", "symbols", "headline"]))


def data_client() -> httpx.Client:
    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id"), "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key")}
    return httpx.Client(base_url="https://data.alpaca.markets", headers=h, timeout=5)


class CappedSms:
    """Fade texts are best-effort and capped per ET day; a failed text never stops the book."""

    def __init__(self, db: OpsStore, event: str = "fade_text", cap: int = MAX_FADE_TEXTS_DAY) -> None:
        self.db, self.sms, self.event, self.cap = db, Sms(), event, cap

    def __call__(self, text: str) -> None:
        day = datetime.now(ET).date()
        since = datetime.combine(day, datetime.min.time(), tzinfo=ET)
        if self.db.count_events(self.event, since) >= self.cap:
            return
        try:
            self.sms.send(text)
            self.db.audit(self.event, n=len(text))
        except Exception as exc:  # noqa: BLE001 - notification only, logged
            log.error("fade text failed: %s", type(exc).__name__)


def to_alerts(text: str, posted_at: datetime, message_id: int) -> list[fade.LiveAlert]:
    out = []
    for it in parse_message(text, posted_at, str(CHANNEL), message_id):
        if isinstance(it, ParsedAlert):
            out.append(fade.LiveAlert(it.symbol, it.alert_type, posted_at, str(CHANNEL), message_id))
        elif isinstance(it, ParsedNews):
            out.append(fade.LiveAlert(it.symbol, "news", posted_at, str(CHANNEL), message_id, it.has_news))
    return out


async def run() -> int:
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    from sgt.db.session import init_db

    db = OpsStore()
    send = CappedSms(db)
    dc, ac = data_client(), asset_client()
    quote, context = iex_quote(dc), iex_context(dc)
    book = borrow_mod.BorrowBook(alpaca_asset=alpaca_asset(ac))
    init_db()
    client = TelegramClient(StringSession(get_secret(SESSION_SECRET)), int(get_secret("telegram_api_id")),
                            get_secret("telegram_api_hash"), device_model=DEVICE)

    collector = TelegramCollector([str(CHANNEL)], "", source=LiveClientSource(client))

    def record(msg) -> None:                   # research tables (forward record); never blocks the book
        try:
            collector.store(str(CHANNEL), to_source(msg))
        except Exception:  # noqa: BLE001 - logged; the hourly poll re-fetches anything missed
            log.exception("storing message %s failed", msg.id)

    @client.on(events.NewMessage(chats=CHANNEL))
    async def on_new(ev):
        posted = ev.message.date.astimezone(timezone.utc)
        for a in to_alerts(ev.message.message or "", posted, ev.message.id):
            log.info("alert %s %s -> %s", a.symbol, a.alert_type, fade.on_alert(db, a))
        record(ev.message)

    @client.on(events.MessageEdited(chats=CHANNEL))
    async def on_edit(ev):                     # research excludes edited alerts; the book keeps the trade, flagged
        with db.tx() as c:
            c.execute("UPDATE fade_trades SET edited=1 WHERE channel=? AND message_id=?", (str(CHANNEL), ev.message.id))
        record(ev.message)

    async def catch_up():
        while True:
            try:
                log.info("channel catch-up stored %d messages", await collector.backfill(FORWARD_START))
            except Exception:  # noqa: BLE001 - keep listening; retried next hour
                log.exception("channel catch-up failed")
            await asyncio.sleep(POLL_EVERY)

    real_send = CappedSms(db, "pilot_text", MAX_PILOT_TEXTS_DAY)
    real = PilotHandle(db, real_send)          # Schwab real-money pilot; None unless SGT_PILOT_SCHWAB=1

    async def loop():
        n = 0
        while True:
            try:
                await asyncio.to_thread(fade.monitor, db, send, quote, None, book.lookup, context)
            except Exception:  # noqa: BLE001 - keep the loop alive, log the stack
                log.exception("monitor failed")
            try:
                pilot = await real.get()
                if pilot is not None:
                    await fade_real.step(pilot, db, quote, real_send, pilot.broker.account_flags)
                    if n % 360 == 0:            # hourly
                        await real.login_check()
            except Exception:  # noqa: BLE001 - real-money step must never stop the paper book; logged
                log.exception("real fade step failed")
            n += 1
            await asyncio.sleep(TICK)

    await client.start()
    if not await client.is_user_authorized():
        raise SystemExit("fade session not authorized; run `fade_live.py login` on the Mac")
    log.info("listening on %s", CHANNEL)
    tasks = [asyncio.create_task(loop()), asyncio.create_task(catch_up())]
    try:
        await client.run_until_disconnected()
    finally:
        for t in tasks:
            t.cancel()
        await real.close()
        dc.close()
        ac.close()
    return 1                                   # disconnected: exit non-zero so the supervisor restarts us


def official(due: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Research scoring for due trades. Rows whose SIP bars are not stored yet are left out (retried).

    Approved / auto-entered trades are scored from their real entry time (amendment F3); ``rule_entry`` /
    ``rule_net`` also score them at the tested timing (alert + 10 s), so the cost of waiting for an approval is
    measured (plan W5, 2026-11-04). Without a delay both equal ``entry`` / ``net``.

    Amendment F5: a trade the owner exited with NO is covered at the last SIP close before the NO exit (same stop
    rule up to then); ``rule_net`` keeps the full tested result, so ``net - rule_net`` is the cost of the NO."""
    now = now or utcnow()
    store = ParquetBarStore(BARS)
    posted = pd.to_datetime(due.posted_at, utc=True)
    a = due.assign(session=pd.to_datetime(due.session).dt.date, has_news=None)
    fetch = AlpacaHistoricalBars(feed="sip").fetch
    for d, g in a.groupby("session"):
        backfill(sorted(g.symbol.unique()), d, d, store=store, fetch=fetch)
    real = posted
    if "entry_after" in due:
        after = pd.to_datetime(due.entry_after, utc=True, errors="coerce") - fade.REACTION
        real = after.where(after.notna(), posted)
    out = _score(a.assign(posted_at=real), store, now)
    if out.empty:
        return out.assign(rule_entry=None, rule_net=None)
    delayed = a.assign(posted_at=posted)[(real != posted).to_numpy()]
    rule = _score(delayed, store, now).set_index("id") if len(delayed) else pd.DataFrame()
    ok = out.qualifies.astype(bool)
    out["rule_entry"] = out.entry.where(ok)
    out["rule_net"] = out.net.where(ok)
    for i, r in out.iterrows():
        if r.id in rule.index:
            q = rule.loc[r.id]
            hit = bool(q["qualifies"])
            out.at[i, "rule_entry"], out.at[i, "rule_net"] = (q["entry"], q["net"]) if hit else (None, None)
    if "reason" in due:
        info = due.set_index("id")
        for i, r in out[ok].iterrows():
            d = info.loc[r.id]
            if d.reason == fade.VETO_EXIT and isinstance(d.est_exit_at, str):
                c = early_cover(store, d.symbol, str(d.session), pd.Timestamp(r.entry_ts), float(r.entry),
                                pd.Timestamp(d.est_exit_at))
                if c is not None:
                    g = (r.entry - c) / r.entry
                    out.at[i, "cover"], out.at[i, "gross"], out.at[i, "net"] = c, g, g - r.cost_rt - LOCATE
    return out


def early_cover(store: ParquetBarStore, symbol: str, session: str, entry_ts: pd.Timestamp, entry: float,
                exit_at: pd.Timestamp) -> float | None:
    """Cover for an early (NO) exit: RTH bars from the entry bar through the last bar that ended by ``exit_at``
    (at least the entry bar), stop rule applied, else that last bar's close."""
    d = date.fromisoformat(session)
    f = store.read_frame(symbol, d, d)
    if f.empty:
        return None
    f = f.assign(ts=pd.to_datetime(f.ts, utc=True))
    end = max(exit_at.tz_convert("UTC").floor("min"), entry_ts + pd.Timedelta(minutes=1))
    return cover(f[(f.segment == "rth") & (f.ts >= entry_ts) & (f.ts < end)], entry, fade.STOP)


def _score(a: pd.DataFrame, store: ParquetBarStore, now: datetime) -> pd.DataFrame:
    t = attach_trades(a[["id", "symbol", "alert_type", "posted_at", "session", "has_news"]], store)
    ev = primary_events(t)
    sc = score(ev.assign(session=ev.session.astype(str)), store) if len(ev) else pd.DataFrame()
    rows = []
    for i, r in t.iterrows():
        late = now - r.posted_at > VOID_AFTER
        if i in ev.index:
            s = sc.iloc[list(ev.index).index(i)]
            ok = pd.notna(s.net)
            if not ok and not late:
                continue                       # window bars missing: retry
            rows.append({"id": r.id, "qualifies": bool(ok), "why": "" if ok else "no bars in window",
                         "entry_ts": r.entry_ts, "entry": s.entry, "cost_rt": s.cost_rt, "gross": s.gross,
                         "net": s.net, "cover": s.entry * (1 - s.gross) if ok else None})
        elif r.status == "no_bars" and not late:
            continue
        else:
            why = r.status if r.status != "ok" else "outside research filters (window / $1-50)"
            rows.append({"id": r.id, "qualifies": False, "why": why, "entry_ts": None, "entry": None,
                         "cost_rt": None, "gross": None, "net": None, "cover": None})
    return pd.DataFrame(rows, columns=["id", "qualifies", "why", "entry_ts", "entry", "cost_rt", "gross", "net",
                                       "cover"])


def cmd_settle() -> int:
    db = OpsStore()
    lines = fade.settle(db, official)
    print(f"fade settle {datetime.now(ET):%Y-%m-%d %H:%M}: " + (", ".join(lines) or "nothing due"))
    return 0


def cmd_borrow() -> int:
    out = borrow_mod.snapshot(datetime.now(timezone.utc))
    print(f"borrow snapshot {out}")
    return 0


def cmd_realism() -> int:
    """Plan W5: live estimate vs official result, alert->entry time, and the cost of waiting for approvals."""
    from sgt.fast import realism

    df = realism.frame(OpsStore())
    if df.empty:
        print("no settled fade trades yet")
        return 0
    cols = ["symbol", "session", "mode", "latency_s", "est_gross", "gross", "gap", "net", "rule_net", "wait_cost",
            "ib_avail"]
    print(df[cols].to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
    print(realism.summary(df))
    return 0


def cmd_login() -> int:
    """Interactive, on the Mac: authorize a NEW session for the owner's account (phone, login code, 2FA password
    if set) and store it in the keychain only. Never reuses data/telegram.session: one auth key on two hosts at once
    risks Telegram revoking both."""
    import keyring
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    with TelegramClient(StringSession(), int(get_secret("telegram_api_id")), get_secret("telegram_api_hash"),
                        device_model=DEVICE) as c:
        me = c.get_me()
        c.get_entity(CHANNEL)                  # fails unless this account has joined the alert channel
        keyring.set_password("sgt", SESSION_SECRET, c.session.save())
    print(f"stored session for Telegram user id {me.id} in keychain sgt/{SESSION_SECRET}")
    return 0


def cmd_push_secret() -> int:
    """Keychain -> Fly secrets via stdin, so values never appear in argv, logs or the terminal. One import =
    one release; the session secret's presence is what makes the boot script start the listener."""
    names = ["telegram_api_id", "telegram_api_hash", SESSION_SECRET]
    body = "".join(f"SGT_SECRET_{n.upper()}={get_secret(n)}\n" for n in names)
    subprocess.run(["fly", "secrets", "import", "-a", "sgt-runner"], input=body, text=True, check=True, timeout=300)
    print("Fly secrets set (values not shown): " + ", ".join(f"SGT_SECRET_{n.upper()}" for n in names))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["login", "push-secret", "run", "settle", "borrow", "realism"])
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stdout)
    if args.cmd == "run":
        return asyncio.run(run())
    return {"login": cmd_login, "push-secret": cmd_push_secret, "settle": cmd_settle, "borrow": cmd_borrow,
            "realism": cmd_realism}[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
