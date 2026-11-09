"""Daily SMS digest (2026-11-02): approved book, research records, broker account, pending, job health."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from sgt.fast import fade, realism
from sgt.ops import approvals
from sgt.ops.book import fmt_targets
from sgt.ops.store import OpsStore

LOGS = {"megacap": Path("reports/megacap_forward/run.log"), "etf": Path("reports/forward/run.log"),
        "rebalance": Path("reports/rebalance/run.log"), "ops": Path("reports/ops/run.log"),
        "sms-poll": Path("reports/ops/poll.log"), "fast": Path("reports/fast/run.log")}
_RUN = re.compile(r"^=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
TAIL = 400


def job_failures(now_et: datetime, logs: dict[str, Path] = LOGS, hours: int = 24) -> list[str]:
    """'<job> <time>' for every run started in the last ``hours`` whose block contains '!!! exit'."""
    out = []
    since = now_et.replace(tzinfo=None) - timedelta(hours=hours)
    for name, p in logs.items():
        if not p.exists():
            continue
        with p.open(errors="replace") as fh:
            lines = fh.readlines()[-TAIL:]
        start = None
        for ln in lines:
            if m := _RUN.match(ln):
                start = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            elif ln.startswith("!!! exit") and start and start >= since:
                out.append(f"{name} {start:%m-%d %H:%M}")
    return out


def megacap_research(root: Path = Path("reports/megacap_forward")) -> str | None:
    p = root / "marks.csv"
    if not p.exists():
        return None
    m = pd.read_csv(p)
    if m.empty or "net" not in m:
        return None
    port = m.groupby("date").net.mean()
    return f"megacap research {len(port)}d: last {port.iloc[-1]:+.2%}, cum {float((1 + port).prod() - 1):+.2%}"


def challenger_research(root: Path = Path("reports/fade_challengers")) -> str | None:
    """Last night's shadow line (prereg_fade_challengers_2026-11-04, also prereg_fade_exits_2026-11-05 under
    reports/fade_exits); None until the first shadow run."""
    p = root / "shadow_summary.json"
    if not p.exists():
        return None
    try:
        line = json.loads(p.read_text()).get("line")
    except (OSError, ValueError):
        return None
    return line if isinstance(line, str) and len(line) <= 300 else None


def book_lines(db: OpsStore, today: date) -> list[str]:
    out = []
    with db.tx() as c:
        strategies = [r[0] for r in c.execute("SELECT DISTINCT strategy FROM book_nav ORDER BY strategy")]
        for s in strategies:
            rows = c.execute("SELECT session, nav, ret, holdings FROM book_nav WHERE strategy=? ORDER BY session",
                             (s,)).fetchall()
            last = rows[-1]
            month0 = next((r["nav"] / (1 + r["ret"]) for r in rows if r["session"][:7] == last["session"][:7]),
                          last["nav"])
            h = json.loads(last["holdings"])
            out.append(f"{s}: ${last['nav']:,.0f} ({last['ret']:+.2%} {last['session'][5:]}, "
                       f"MTD {last['nav'] / month0 - 1:+.2%}) {fmt_targets(h) if h else 'flat'}")
    return out


def build(db: OpsStore, now_et: datetime, *, fills: list[dict], problems: list[str],
          broker: str | None, research: list[str]) -> str:
    lines = [f"[SGT] {now_et:%a %m-%d} digest" + ("  ** PAUSED **" if db.paused() else "")]
    lines += ["Your book:"] + (book_lines(db, now_et.date()) or ["  (no approved positions yet)"])
    lines += fade.digest_lines(db, now_et.date(), now_et)
    try:
        from sgt.fast import scanner                  # experiment book (prereg_scanner_book_2026-11-09)

        sl = scanner.summary(db, now_et.date().isoformat())
        if sl:
            lines.append(sl)
    except Exception:  # noqa: BLE001 - one digest line must never block the digest
        lines.append("scanner: summary unavailable")
    lines += realism.lines(db)
    if fills:
        lines.append("Fills: " + "; ".join(f"{f['strategy']} {f['symbol']} -> {f['target']:+.0%} @ {f['price']:.2f}"
                                           for f in fills))
    if broker:
        lines.append(broker)
    lines += [r for r in research if r]
    pend = approvals.pending(db)
    if pend:
        lines.append("Pending: " + "; ".join(f"{p['strategy']} OK {p['code']} by {approvals.fmt_et(datetime.fromisoformat(p['expires_at']))}"
                                             for p in pend))
    stuck = approvals.attention(db, now_et - timedelta(hours=24))
    if stuck:
        lines.append("Attention: " + "; ".join(f"#{r['id']} {r['strategy']} {r['status']}" for r in stuck))
    fails = job_failures(now_et)
    lines.append("Jobs: " + ("FAILED " + ", ".join(fails) if fails else "all OK (24h)"))
    lines += [f"! {p}" for p in problems]
    return "\n".join(lines)
