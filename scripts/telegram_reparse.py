"""Re-parse stored raw Telegram messages into crowd_alerts after a grammar change.

crowd_alerts is derived data: rows for each re-parsed channel are rebuilt from
telegram_messages.raw_text (never modified). Run after editing telegram_parser.py.

Usage:
    python scripts/telegram_reparse.py --channel=-1001254273411
"""
from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import delete, select

from sgt.crowd.models import CrowdAlert, TelegramMessage
from sgt.crowd.telegram_parser import ParsedAlert, ParsedNews, parse_message
from sgt.db.session import get_session, init_db
from sgt.util import to_utc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", required=True)
    args = ap.parse_args()
    init_db()
    counts: Counter[str] = Counter()
    with get_session() as session:
        session.execute(delete(CrowdAlert).where(CrowdAlert.channel == args.channel))
        msgs = session.execute(
            select(TelegramMessage).where(TelegramMessage.channel == args.channel)
        ).scalars()
        for m in msgs:
            posted = to_utc(m.posted_at)
            items = [it for it in parse_message(m.raw_text, posted, m.channel, m.message_id)
                     if isinstance(it, (ParsedAlert, ParsedNews))]
            m.parse_status = "parsed" if items else "unparsed"
            counts[m.parse_status] += 1
            for it in items:
                news = isinstance(it, ParsedNews)
                session.add(CrowdAlert(
                    channel=m.channel, message_id=m.message_id, idx=it.idx, symbol=it.symbol,
                    alert_type="news" if news else it.alert_type,
                    price=None if news else it.price, posted_at=posted,
                    has_news=it.has_news if news else None,
                    headline=(it.headline or None) if news else None,
                ))
                counts[f"alert:{'news' if news else it.alert_type}"] += 1
    for k, v in sorted(counts.items()):
        print(f"{k:28s} {v}")


if __name__ == "__main__":
    main()
