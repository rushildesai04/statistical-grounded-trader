"""Run the live MetaTradings signal listener -> PAPER trading (or observe only).

Usage:
    .venv/bin/python scripts/signals_live.py --observe     # journal + notify, never orders
    .venv/bin/python scripts/signals_live.py               # trade the signals paper account

Needs: an authorized Telegram session (scripts/telegram_login.py); for trading, the
second paper account's keys in keychain service sgt as alpaca_signals_key_id /
alpaca_signals_secret_key. SMS is enabled when twilio_account_sid, twilio_auth_token,
twilio_from_number and owner_phone are in the keychain. Owner commands (Saved Messages
or SMS): status | pause | resume | kill.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from sgt.config import get_settings
from sgt.core.secrets import get_secret
from sgt.crowd.telegram_collector import TelegramCollector, telegram_session_lock
from sgt.db.session import init_db
from sgt.signals_live.broker import ObserveBroker, PaperBroker
from sgt.signals_live.notify import Fanout, TelegramSelfNotifier, TwilioSms
from sgt.signals_live.rules import Limits
from sgt.signals_live.runner import JOURNAL, Runner

NVB = -1001254273411      # "NEWS , VOLUME , BREAKOUTS"
IDEAS = -1001204455879    # "OPTIONS & STOCKS TRADE IDEAS"
log = logging.getLogger("signals_live")


def _sms() -> TwilioSms | None:
    names = ("twilio_account_sid", "twilio_auth_token", "twilio_from_number", "owner_phone")
    if all(get_secret(n, required=False) for n in names):
        return TwilioSms()
    log.info("SMS disabled (twilio secrets not all present)")
    return None


async def main_async(args) -> int:
    from telethon import TelegramClient, events

    s = get_settings()
    client = TelegramClient(s.telegram_session_path, int(get_secret("telegram_api_id")),
                            get_secret("telegram_api_hash"), timeout=20)
    await asyncio.wait_for(client.connect(), 30)
    if not await client.is_user_authorized():
        raise SystemExit("Telegram session not authorized; run scripts/telegram_login.py")
    broker = ObserveBroker() if args.observe else PaperBroker()
    sms = _sms()
    notify = Fanout(TelegramSelfNotifier(client), sms)
    limits = Limits(per_trade_usd=args.per_trade, max_open=args.max_open, daily_loss_stop_usd=args.daily_loss)
    runner = Runner(client, broker, notify, sms, nvb=NVB, ideas=IDEAS, limits=limits,
                    collector=TelegramCollector([str(NVB), str(IDEAS)], s.telegram_session_path),
                    trade_alerts=args.trade_alerts, trade_ideas=args.trade_ideas)

    @client.on(events.NewMessage(chats=[NVB, IDEAS]))
    async def _signal(ev):  # noqa: ANN001
        m = ev.message
        await runner.on_message(ev.chat_id, m.id, m.date, m.message or "", bool(m.edit_date))

    @client.on(events.NewMessage(chats="me"))
    async def _cmd(ev):  # noqa: ANN001
        await runner.on_saved_message(ev.message.message or "")

    await runner.reconcile()
    mode = "OBSERVE (no orders)" if args.observe else "PAPER TRADING"
    alerts = "traded" if args.trade_alerts else "watch-only"
    ideas = "traded" if args.trade_ideas else "watch-only"
    await runner.tell(f"signals bot started: {mode}; stock calls {ideas}, news/volume alerts {alerts}. ${args.per_trade:,.0f}/trade, max {args.max_open} open, "
                      f"daily stop -${args.daily_loss:,.0f}. Commands: status | pause | resume | kill")
    try:
        await runner.run_loops()
    finally:
        await broker.aclose()
        await client.disconnect()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--observe", action="store_true", help="never place orders")
    ap.add_argument("--per-trade", type=float, default=2_000.0)
    ap.add_argument("--max-open", type=int, default=5)
    ap.add_argument("--daily-loss", type=float, default=1_000.0)
    ap.add_argument("--trade-ideas", action="store_true",
                    help="paper-trade stock calls (failed the 2026-10-29 test; off by default)")
    ap.add_argument("--trade-alerts", action="store_true",
                    help="also trade NEWS/VOLUME/BREAKOUTS alerts (failed the 2026-10-29 test; off by default)")
    args = ap.parse_args()
    JOURNAL.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(JOURNAL / "run.log"), logging.StreamHandler()])
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()
    with telegram_session_lock(get_settings().telegram_session_path):
        return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
