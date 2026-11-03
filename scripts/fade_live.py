"""Live alert-fade book on the runner (2026-11-03; rule: docs/findings/prereg_alert_fade_2026-11-01.md).

    python scripts/fade_live.py login     # on the Mac, once: dedicated Telegram account -> keychain sgt/tg_fade_session
    python scripts/fade_live.py push-secret   # keychain -> Fly secret over stdin (never printed)
    python scripts/fade_live.py run       # Fly: listen to the alert channel, enter/exit estimates, texts
    python scripts/fade_live.py settle    # Fly cron 20:40 ET: official results from SIP bars (research code)

The listener uses a DEDICATED Telegram account (user decision 2026-11-03) whose session string lives only in
the keychain and the Fly secret SGT_SECRET_TG_FADE_SESSION. Entries need ARM for the day (SMS), see
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from alert_fade_test import score
from backfill_alpaca import backfill
from sgt.core.secrets import get_secret
from sgt.crowd.telegram_parser import ParsedAlert, ParsedNews, parse_message
from sgt.fast import fade
from sgt.intraday.sources import AlpacaHistoricalBars
from sgt.intraday.store import ParquetBarStore
from sgt.ops.sms import Sms
from sgt.ops.store import OpsStore, utcnow
from telegram_alert_test import attach_trades, primary_events

log = logging.getLogger("fade_live")
ET = ZoneInfo("America/New_York")
CHANNEL = -1001254273411                       # MetaTradings alerts (same as alert_fade_forward.CHANNEL)
SESSION_SECRET = "tg_fade_session"
BARS = Path("data/fast")                       # own store: never mixes with research bar stores
TICK = 30                                      # seconds between monitor passes
MAX_FADE_TEXTS_DAY = 40
VOID_AFTER = timedelta(days=3)                 # SIP bars still missing after this -> void
_SYMBOL = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def iex_quote(client: httpx.Client):
    def quote(symbol: str) -> float | None:
        if not _SYMBOL.match(symbol):
            return None
        try:
            r = client.get(f"/v2/stocks/{symbol}/trades/latest", params={"feed": "iex"})
            if r.status_code != 200:
                return None
            p = float(r.json().get("trade", {}).get("p", 0))
            return p if p > 0 else None
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning("quote %s failed: %s", symbol, type(exc).__name__)
            return None
    return quote


def data_client() -> httpx.Client:
    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id"), "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key")}
    return httpx.Client(base_url="https://data.alpaca.markets", headers=h, timeout=5)


class CappedSms:
    """Fade texts are best-effort and capped per ET day; a failed text never stops the book."""

    def __init__(self, db: OpsStore) -> None:
        self.db, self.sms = db, Sms()

    def __call__(self, text: str) -> None:
        day = datetime.now(ET).date()
        since = datetime.combine(day, datetime.min.time(), tzinfo=ET)
        if self.db.count_events("fade_text", since) >= MAX_FADE_TEXTS_DAY:
            return
        try:
            self.sms.send(text)
            self.db.audit("fade_text", n=len(text))
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

    db = OpsStore()
    send = CappedSms(db)
    dc = data_client()
    quote = iex_quote(dc)
    client = TelegramClient(StringSession(get_secret(SESSION_SECRET)), int(get_secret("telegram_api_id")),
                            get_secret("telegram_api_hash"))

    @client.on(events.NewMessage(chats=CHANNEL))
    async def on_new(ev):
        posted = ev.message.date.astimezone(timezone.utc)
        for a in to_alerts(ev.message.message or "", posted, ev.message.id):
            log.info("alert %s %s -> %s", a.symbol, a.alert_type, fade.on_alert(db, a))

    @client.on(events.MessageEdited(chats=CHANNEL))
    async def on_edit(ev):                     # research excludes edited alerts; the book keeps the trade, flagged
        with db.tx() as c:
            c.execute("UPDATE fade_trades SET edited=1 WHERE channel=? AND message_id=?", (str(CHANNEL), ev.message.id))

    async def loop():
        while True:
            try:
                await asyncio.to_thread(fade.monitor, db, send, quote)
            except Exception:  # noqa: BLE001 - keep the loop alive, log the stack
                log.exception("monitor failed")
            await asyncio.sleep(TICK)

    await client.start()
    if not await client.is_user_authorized():
        raise SystemExit("fade session not authorized; run `fade_live.py login` on the Mac")
    log.info("listening on %s", CHANNEL)
    task = asyncio.create_task(loop())
    try:
        await client.run_until_disconnected()
    finally:
        task.cancel()
        dc.close()
    return 1                                   # disconnected: exit non-zero so the supervisor restarts us


def official(due: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Research scoring for due trades. Rows whose SIP bars are not stored yet are left out (retried)."""
    now = now or utcnow()
    store = ParquetBarStore(BARS)
    a = due.assign(posted_at=pd.to_datetime(due.posted_at, utc=True),
                   session=pd.to_datetime(due.session).dt.date, has_news=None)
    fetch = AlpacaHistoricalBars(feed="sip").fetch
    for d, g in a.groupby("session"):
        backfill(sorted(g.symbol.unique()), d, d, store=store, fetch=fetch)
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


def cmd_login() -> int:
    """Interactive, on the Mac: log in the dedicated account and store the session in the keychain only."""
    import keyring
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    with TelegramClient(StringSession(), int(get_secret("telegram_api_id")), get_secret("telegram_api_hash")) as c:
        me = c.get_me()
        c.get_entity(CHANNEL)                  # fails unless this account has joined the alert channel
        keyring.set_password("sgt", SESSION_SECRET, c.session.save())
    print(f"stored session for Telegram user id {me.id} in keychain sgt/{SESSION_SECRET}")
    return 0


def cmd_push_secret() -> int:
    """Keychain -> Fly secret via stdin, so the value never appears in argv, logs or the terminal."""
    value = get_secret(SESSION_SECRET)
    subprocess.run(["fly", "secrets", "import", "-a", "sgt-runner"], input=f"SGT_SECRET_TG_FADE_SESSION={value}\n",
                   text=True, check=True, timeout=300)
    print("Fly secret SGT_SECRET_TG_FADE_SESSION set (value not shown)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["login", "push-secret", "run", "settle"])
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stdout)
    if args.cmd == "run":
        return asyncio.run(run())
    return {"login": cmd_login, "push-secret": cmd_push_secret, "settle": cmd_settle}[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
