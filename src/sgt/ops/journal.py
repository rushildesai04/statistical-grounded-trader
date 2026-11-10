"""Trade journal (owner 2026-11-09): every trade with its facts, the owner's notes, and the AI analyst's insights.

Notes are owner-typed free text; insights are model output. Both are UNTRUSTED text: stored as-is (bounded), rendered
with textContent on the page, never interpreted, never used to change a trading rule without a pre-registered test.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from sgt.ops.store import OpsStore, iso

KEY_RE = re.compile(r"^(fade|scan|book|long):[1-9][0-9]{0,8}$")
MAX_NOTE = 5000
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TABLE = {"fade": "fade_trades", "scan": "scan_trades", "book": "book_orders", "long": "long_trades"}
FADE_COLS = ("symbol", "session", "alert_type", "posted_at", "status", "reason", "est_entry", "est_entry_at", "est_exit",
             "est_exit_at", "entry", "cover", "net", "conf", "news_own", "news_head", "filing_dilution", "filing_form",
             "ib_avail", "alp_shortable", "st_n60", "x_n60", "x_cat24", "x_cat_posts", "rd_rank", "rd_mentions")
SCAN_COLS = ("symbol", "session", "bar_ts", "status", "reason", "signal_px", "est_entry", "est_entry_at", "est_exit",
             "est_exit_at", "entry", "cover", "net")
BOOK_COLS = ("strategy", "symbol", "fill_session", "target", "status", "fill_price", "label")
# Live paper long book (2026-11-10: longs were missing from the journal, so the per-trade AI review, the nightly brain
# and the Notes tab never saw them). Official entry/exit come from the long shadow once settled; ``cover`` = the sell.
LONG_SQL = ("SELECT t.id, t.symbol, t.session, t.kind, t.status, t.reason, t.trigger, t.stop, t.target, t.est_entry, "
            "t.entry_ts AS est_entry_at, t.est_exit, t.est_exit_at, t.net, f.posted_at, f.alert_type, "
            "CASE WHEN t.status='settled' THEN s.entry END AS entry, CASE WHEN t.status='settled' THEN s.exit END AS cover "
            "FROM long_trades t JOIN fade_trades f ON f.id=t.fade_id "
            "LEFT JOIN long_shadow s ON s.fade_id=t.fade_id AND s.kind=t.kind "
            "WHERE t.status IN ('open','est_closed','settled','void') ORDER BY t.id DESC LIMIT ?")


def clean(text: str) -> str:
    """Bounded, control-character-free note text (newlines and tabs kept)."""
    return _CTRL.sub("", text)[:MAX_NOTE]


def entries(db: OpsStore, limit: int = 500) -> list[dict]:
    """Every trade worth a note, newest first: entered fades, scanner trades, long-book trades, model-book orders that
    filled."""
    out = []
    with db.tx() as c:
        for r in c.execute("SELECT id, " + ", ".join(FADE_COLS) + " FROM fade_trades WHERE status IN "  # noqa: S608
                           "('open','est_closed','settled','void') ORDER BY id DESC LIMIT ?", (limit,)):
            out.append({"key": f"fade:{r['id']}", "book": "alert fade", **{k: r[k] for k in FADE_COLS}})
        for r in c.execute("SELECT id, " + ", ".join(SCAN_COLS) + " FROM scan_trades WHERE status IN "  # noqa: S608
                           "('open','est_closed','settled','void') ORDER BY id DESC LIMIT ?", (limit,)):
            out.append({"key": f"scan:{r['id']}", "book": "scanner", **{k: r[k] for k in SCAN_COLS}})
        for r in c.execute(LONG_SQL, (limit,)):
            row = dict(r)
            out.append({"key": f"long:{row.pop('id')}", "book": "long book", "side": "long", **row})
        for r in c.execute("SELECT id, " + ", ".join(BOOK_COLS) + " FROM book_orders WHERE status IN "  # noqa: S608
                           "('filled','scheduled') AND source != 'void' "
                           "ORDER BY id DESC LIMIT ?", (limit,)):
            out.append({"key": f"book:{r['id']}", "book": r["strategy"], "session": r["fill_session"],
                        **{k: r[k] for k in BOOK_COLS}})
    return sorted(out, key=lambda e: (str(e.get("session") or ""), e["key"]), reverse=True)


def exists(db: OpsStore, key: str) -> bool:
    if not KEY_RE.match(key or ""):
        return False
    kind, rid = key.split(":")
    with db.tx() as c:
        return c.execute(f"SELECT 1 FROM {_TABLE[kind]} WHERE id=?", (int(rid),)).fetchone() is not None  # noqa: S608


def save(db: OpsStore, key: str, text: str, now: datetime) -> dict:
    """Upsert one note (empty text deletes it); every edit kept in history. Raises ValueError on bad input."""
    if not isinstance(key, str) or not isinstance(text, str) or not exists(db, key):
        raise ValueError("unknown trade")
    if len(text) > MAX_NOTE:
        raise ValueError(f"note over {MAX_NOTE} characters")
    t, at = clean(text).strip(), iso(now)
    with db.tx() as c:
        c.execute("INSERT INTO trade_notes_history(key, text, at) VALUES (?,?,?)", (key, t, at))
        if t:
            c.execute("INSERT INTO trade_notes(key, text, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                      "text=excluded.text, updated_at=excluded.updated_at", (key, t, at))
        else:
            c.execute("DELETE FROM trade_notes WHERE key=?", (key,))
    return {"key": key, "text": t, "updated_at": at}


def notes(db: OpsStore) -> dict[str, dict]:
    with db.tx() as c:
        return {r["key"]: {"text": r["text"], "updated_at": r["updated_at"]}
                for r in c.execute("SELECT key, text, updated_at FROM trade_notes")}


def notes_since(db: OpsStore, since: str | None) -> dict[str, dict]:
    with db.tx() as c:
        rows = c.execute("SELECT key, text, updated_at FROM trade_notes WHERE updated_at > ?", (since or "",)).fetchall()
    return {r["key"]: {"text": r["text"], "updated_at": r["updated_at"]} for r in rows}


def add_insight(db: OpsStore, model: str, scope: str, body: dict, now: datetime) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO trade_insights(run_at, model, scope, body) VALUES (?,?,?,?)",
                  (iso(now), model, scope, json.dumps(body)))


def insights(db: OpsStore, limit: int = 30) -> list[dict]:
    with db.tx() as c:
        rows = c.execute("SELECT run_at, model, scope, body FROM trade_insights ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    out = []
    for r in rows:
        try:
            body = json.loads(r["body"])
        except ValueError:
            continue
        out.append({"run_at": r["run_at"], "model": r["model"], "scope": r["scope"], "body": body})
    return out


REVIEWED = "insights_reviewed_id"


def pending_tests(db: OpsStore) -> int:
    """Proposed tests from AI runs the agent has not reviewed yet (learning-loop audit F3: insight -> test queue)."""
    seen = int(db.flag(REVIEWED) or 0)
    with db.tx() as c:
        rows = c.execute("SELECT body FROM trade_insights WHERE id > ?", (seen,)).fetchall()
    n = 0
    for r in rows:
        try:
            n += sum(1 for d in json.loads(r["body"]).get("distilled", []) if d.get("proposed_test"))
        except (ValueError, AttributeError):
            continue
    return n


def mark_reviewed(db: OpsStore) -> int:
    """Record that every insight so far was reviewed (each accepted idea gets a prereg in docs/findings)."""
    with db.tx() as c:
        top = c.execute("SELECT COALESCE(MAX(id), 0) FROM trade_insights").fetchone()[0]
    db.set_flag(REVIEWED, str(top))
    return int(top)
