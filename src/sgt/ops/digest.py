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
from sgt.ops.sms_style import bold, italic
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


# Owner 2026-11-09: "format the digest better", then "add emojis, bolds, italics". Layout: the day's dollars first,
# then what needs the owner, then one line per book, then the record. Styled text makes the SMS UCS-2 (67 characters
# per segment, and each bold/italic letter is 2 UTF-16 units), so bold is kept to headers and book names. Reply codes
# are never styled: a copied styled digit is not an ASCII digit and would not parse. Long digests go as numbered parts.
NAMES = {"etf:B_6040": "60/40", "etf:F1_dual_momentum": "dual momentum", "etf:F2_6040_trend": "60/40 trend",
         "megacap": "megacap", "megacap_overlay": "megacap overlay", "megacap_ext": "megacap 15", "megacap_long": "megacap long calls"}
_CTRL = {i: None for i in range(32) if i != 10} | {127: None}


def clean(text: str) -> str:
    """Drop control characters (other than newlines) from text that reaches the phone."""
    return text.translate(_CTRL)


def units(text: str) -> int:
    """SMS length as carriers count it in UCS-2: UTF-16 code units (emoji and styled letters are 2)."""
    return len(text.encode("utf-16-le")) // 2


def dot(x: float) -> str:
    return "🟢" if x > 0.5 else "🔴" if x < -0.5 else "⚪"


def usd(x: float) -> str:
    return f"{'-' if x < -0.5 else '+'}${abs(x):,.0f}"


def name(strategy: str) -> str:
    return NAMES.get(strategy, strategy.removeprefix("etf:"))


def holdings(h: dict[str, float]) -> str:
    """{'META': -.25, 'MSFT': -.25, 'NVDA': -.2} -> 'short META+MSFT 25%, NVDA 20%'."""
    out = []
    for side, sign in (("long", 1), ("short", -1)):
        legs = sorted(((s, abs(w)) for s, w in h.items() if w * sign > 1e-6), key=lambda x: (-x[1], x[0]))
        if not legs:
            continue
        groups: list[tuple[list[str], float]] = []
        for s, w in legs:
            if groups and round(groups[-1][1] * 100) == round(w * 100):
                groups[-1][0].append(s)
            else:
                groups.append(([s], w))
        out.append(f"{side} " + ", ".join(f"{'+'.join(g)} {w:.0%}" for g, w in groups))
    return "; ".join(out) or "flat"


# Owner 2026-11-14 (interview): the headline counts TRADING books only. The two long-and-short model tests and the
# overlay are measurement (model short calls have no expected money: prereg_record_score_2026-11-14; the tests keep
# running untouched for their evidence); the ETF books are yardsticks. Both are shown, on their own line, not added.
TRADING, MEASURE, YARD = "long calls", "model tests", "yardsticks"
NOT_COUNTED = {MEASURE: "measurement only", YARD: "yardsticks"}


def group(strategy: str) -> str:
    """The headline group of a daily book. An unknown book counts as trading: never silently left out."""
    if strategy.startswith("etf:"):
        return YARD
    if strategy == "megacap_long":
        return TRADING
    return MEASURE if strategy.startswith("megacap") else "books"


def model_books(db: OpsStore, day: date) -> tuple[list[str], dict[str, float]]:
    """One line per daily book (trading first, then measurement, then yardsticks, each of the last two under a label)
    and today's dollars per headline group."""
    out, total = [], {TRADING: 0.0, MEASURE: 0.0, YARD: 0.0}
    order = {MEASURE: 1, YARD: 2}
    seen: set[str] = set()
    with db.tx() as c:
        strategies = [r[0] for r in c.execute("SELECT DISTINCT strategy FROM book_nav ORDER BY strategy")]
        for s in sorted(strategies, key=lambda x: (order.get(group(x), 0), x)):
            g = group(s)
            if g in NOT_COUNTED and g not in seen:
                seen.add(g)
                out.append(italic(f"{NOT_COUNTED[g]}, not in the total:"))
            rows = c.execute("SELECT session, nav, ret, holdings FROM book_nav WHERE strategy=? ORDER BY session",
                             (s,)).fetchall()
            last = rows[-1]
            month0 = next((r["nav"] / (1 + r["ret"]) for r in rows if r["session"][:7] == last["session"][:7]),
                          last["nav"])
            mtd = last["nav"] / month0 - 1
            h = json.loads(last["holdings"] or "{}")
            if last["session"] == day.isoformat():
                today = last["nav"] - last["nav"] / (1 + last["ret"])
                total[g] = total.get(g, 0.0) + today
                head = f"{dot(today)} {bold(name(s))} {usd(today)} ({last['ret']:+.2%})"
            elif not {k: w for k, w in h.items() if abs(w) > 1e-6} and not c.execute(
                    "SELECT 1 FROM book_orders WHERE strategy=? AND status='scheduled' AND fill_session<=?",
                    (s, day.isoformat())).fetchone():
                # 2026-11-13: a book holding nothing with no order due is never marked (book.settle skips it); it made
                # $0 today. "no close yet" read as a missed job on 11-12 and 11-13 while megacap was flat on purpose.
                head = f"\u26AA {bold(name(s))} $0 today, {italic('flat')} since the {last['session'][5:]} close"
            else:
                head = f"\u23F8\uFE0F {bold(name(s))} {italic('no close yet')} (last {last['session'][5:]})"
            out.append(f"{head}, month {mtd:+.1%}, ${last['nav']:,.0f}: {holdings(h)}")
    return out, total


HOLD_SETS = (("megacap", "megacap_forward/marks.csv"), ("megacap 15", "megacap_ext/marks.csv"))
MAX_MARKS_BYTES = 5_000_000


def hold_lines(db: OpsStore) -> list[str]:
    """RECORD lines: each daily model's forward ledger next to simply holding the same names (review 2026-11-13: a
    +4% model fortnight means little without what holding did; the model beat holding on none of 20 names in its
    2018-2026 record). Equal weight, compounded per session, from the forward marks (ret = hold, net = model after
    costs). A missing file gives no line; an unreadable one says so."""
    import csv

    out = []
    root = Path(db.path).resolve().parent.parent
    for label, rel in HOLD_SETS:
        p = root / rel
        if not p.exists():
            continue
        try:
            if p.stat().st_size > MAX_MARKS_BYTES:
                raise ValueError("too large")
            days: dict[str, list[tuple[float, float]]] = {}
            with p.open(newline="") as fh:
                for r in csv.DictReader(fh):
                    if r["void"] == "True":
                        continue
                    days.setdefault(r["date"], []).append((float(r["net"]), float(r["ret"])))
            model = hold = 1.0
            for d in sorted(days):
                model *= 1 + sum(x[0] for x in days[d]) / len(days[d])
                hold *= 1 + sum(x[1] for x in days[d]) / len(days[d])
            if days:
                out.append(f"{bold(label)} vs holding the same names, {len(days)} sessions since {min(days)[5:]}: "
                           f"model {model - 1:+.1%}, holding {hold - 1:+.1%}")
        except (OSError, KeyError, ValueError, csv.Error):
            out.append(f"{label} vs holding: unavailable")
    from sgt.megacap import longonly

    lo = longonly.ledger(root)                          # prereg_megacap_long_forward_2026-11-13 (the deciding ledger)
    if lo["sessions"]:
        avg = f"{lo['avg_call']:+.2%}" if lo["avg_call"] is not None else "none yet"
        out.append(f"{bold('megacap long calls')} ledger, {lo['sessions']} sessions since {lo['since'][5:]}: "
                   f"{lo['total']:+.1%} ({lo['calls']} calls, {lo['wins']} winners, avg {avg}; tested "
                   f"{longonly.TESTED_AVG:+.2%}, {longonly.TESTED_WIN:.0%}); long and short {lo['long_short']:+.1%}, "
                   f"holding {lo['holding']:+.1%}" + (" -> BELOW THE -5% STOP RULE" if lo["stop"] else ""))
    return out


def fade_today(db: OpsStore, day: date) -> tuple[list[str], float]:
    s = day.isoformat()
    with db.tx() as c:
        rows = c.execute("SELECT symbol, status, net FROM fade_trades WHERE session=? AND status IN "
                         "('settled','void','open','pending','est_closed') ORDER BY id", (s,)).fetchall()
        off = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=? AND status='filtered' AND reason=?",
                        (s, fade.OFF_REASON)).fetchone()[0]
    head = ([f"⏸️ {bold('fade')} {italic('OFF')} (owner 11-12, shorts deprioritized): {off} alerts not "
             "shorted today; the forward record still scores every alert"] if fade.is_off(db) or off else [])
    if not rows:
        return head, 0.0
    today = fade.nav(db, day) - fade.nav(db, day - timedelta(days=1))
    st = [r for r in rows if r["status"] == "settled"]
    bits = [f"{r['symbol']} {r['net']:+.1%}" for r in st]
    bits += [f"{r['symbol']} void" for r in rows if r["status"] == "void"]
    wait = sum(r["status"] != "settled" and r["status"] != "void" for r in rows)
    line = (f"{dot(today)} {bold('fade')} {usd(today)}, {len(st)} trades: " + ", ".join(bits)
            + (f" ({italic(f'{wait} not settled yet')})" if wait else ""))
    return head + [line], today


def long_today(db: OpsStore, day: date) -> tuple[list[str], float]:
    """Long book TODAY line with its official dollars (2026-11-12 night check: the line was always green and its
    dollars were left out of the header total, so a losing long day read as a win)."""
    from sgt.fast import long_book

    lt = long_book.today_line(db, day.isoformat())
    off = ([f"\u23F8\uFE0F {bold('longs')} {italic('OFF')} (owner 11-13, measure only): the long shadow still scores "
            "every alert"] if long_book.is_off(db) else [])
    if not lt:
        return off, 0.0
    today = long_book.nav(db, day) - long_book.nav(db, day - timedelta(days=1))
    with db.tx() as c:
        settled = c.execute("SELECT COUNT(*) FROM long_trades WHERE session=? AND status='settled'",
                            (day.isoformat(),)).fetchone()[0]
    money = f"{usd(today)}, " if settled else ""                    # estimates only: no dollars invented
    return off + [f"{dot(today)} {bold('longs')} {money}" + lt.removeprefix("longs ")], today


def scanner_today(db: OpsStore, day: date) -> tuple[list[str], float]:
    from sgt.fast import scanner

    with db.tx() as c:
        rows = c.execute("SELECT symbol, weight, net FROM scan_trades WHERE session=? AND status='settled' "
                         "ORDER BY id", (day.isoformat(),)).fetchall()
    if not rows:
        return [], 0.0
    g = sum(r["weight"] * r["net"] for r in rows)
    v = scanner.nav(db)
    today = v - v / (1 + g)                      # close enough for display: one compounding step
    return [f"{dot(today)} {bold('scanner')} {usd(today)} ({italic('experiment, 0.5%/trade')}): "
            + ", ".join(f"{r['symbol']} {r['net']:+.1%}" for r in rows)], today


def _safe(fn, fallback: str, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - one digest section must never block the digest
        return [fallback], 0.0


def build(db: OpsStore, now_et: datetime, *, fills: list[dict], problems: list[str],
          broker: str | None, research: list[str]) -> str:
    day = now_et.date()
    books, by_group = _safe(model_books, "books: unavailable", db, day)
    fl, fade_usd = _safe(fade_today, "fade: today unavailable", db, day)
    sc, scan_usd = _safe(scanner_today, "scanner: today unavailable", db, day)
    longs, long_usd = _safe(long_today, "longs: unavailable", db, day)      # owner 2026-11-10 option A: longs first
    by_group = by_group if isinstance(by_group, dict) else {}
    aside = {k: by_group.get(k, 0.0) for k in NOT_COUNTED}                 # shown, never added (owner 2026-11-14)
    groups = {**{k: v for k, v in by_group.items() if k not in NOT_COUNTED}, "fade": fade_usd, "scanner": scan_usd,
              "longs": long_usd}
    total = sum(groups.values())

    needs = (["PAUSED: no new trades until RESUME"] if db.paused() else [])
    needs += [w.removeprefix("! ") for w in fade.digest_warnings(db, day, now_et)]
    for p in approvals.pending(db):
        needs.append(f"pending {p['strategy']}: OK {p['code']} by {approvals.fmt_et(datetime.fromisoformat(p['expires_at']))}")
    stuck = approvals.attention(db, now_et - timedelta(hours=24))
    needs += [f"approval #{r['id']} {r['strategy']} {r['status']}" for r in stuck]
    fails = job_failures(now_et, LOGS)
    if fails:
        needs.append("jobs FAILED: " + ", ".join(fails))
    needs += list(problems)
    record: list[str] = []
    try:                                              # learning-loop audit F1: live vs tested, per book
        from sgt.learning import live_monitor

        res = live_monitor.check_all(db)
        for r in res:
            ln = live_monitor.line(r)
            if ln is None:
                continue
            if r["alarms"] or r.get("error"):
                needs.append(ln.removeprefix("! "))
            elif "expected" in r and not r["book"].startswith("scanner"):     # scanner: its RECORD line says it
                record.append(ln)
        needs += live_monitor.guard_lines(db, res, day)      # size cuts and standing guard state (never silent)
    except Exception:  # noqa: BLE001
        needs.append("live monitor unavailable")

    split = ", ".join(f"{k} {usd(v)}" for k, v in sorted(groups.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 0.5)
    not_counted = ", ".join(f"{k} {usd(v)}" for k, v in aside.items() if abs(v) >= 0.5)
    lines = [f"[SGT] \U0001F4CA {bold(f'{now_et:%a %m-%d}')}: {bold(usd(total))} today {italic('(paper, trading books)')}"
             + (f"\n{split}" if split else "") + (f"\n{italic('not in the total:')} {not_counted}" if not_counted else "")]
    lines += ((["", f"\U0001F6A8 {bold('NEEDS YOU')}"] + [f"\u2022 {n}" for n in needs]) if needs else
              ["\u2705 Nothing needs you. Jobs OK (24h)."])
    lines += ["", f"\U0001F4B5 {bold('TODAY')}"] + longs + fl + sc + books
    lines += ["", f"\U0001F4C8 {bold('RECORD')}"]
    try:
        from sgt.fast import long_book

        lr = long_book.record_line(db)
        if lr:
            lines.append(f"{bold('long book')} " + lr.removeprefix("long book "))
    except Exception:  # noqa: BLE001 - informational line
        lines.append("long book: record unavailable")
    lines += hold_lines(db)
    fr = fade.record_line(db)
    if fr:
        lines.append(f"{bold('fade')} " + fr.removeprefix("fade "))
    try:
        from sgt.fast import scanner

        sl = scanner.record_line(db)
        if sl:
            lines.append(f"{bold('scanner')} " + sl.removeprefix("scanner "))
    except Exception:  # noqa: BLE001
        lines.append("scanner: record unavailable")
    try:                                              # owner 2026-11-10: paper-run the longs
        from sgt.fast import long_shadow

        ls = long_shadow.record_line(db)
        if ls:
            lines.append(f"{bold('long shadow')} " + ls.removeprefix("long shadow "))
    except Exception:  # noqa: BLE001 - informational line
        lines.append("long shadow: record unavailable")
    lines += [f"{bold('live vs tested')}: {r}" for r in record]
    try:
        lines += [f"{bold('fade exec')}: " + x.removeprefix("fade exec: ") for x in realism.lines(db)]
    except Exception:  # noqa: BLE001 - one digest line must never block the digest
        lines.append("fade exec: unavailable")
    ai = [x for x in (db.flag("analyst_status") and f"report {db.flag('analyst_status')}",
                      db.flag("ai_review_status") and f"reviews {db.flag('ai_review_status')}") if x]
    try:
        from sgt.ops import journal

        pt = journal.pending_tests(db)
        if pt:
            ai.append(f"{pt} proposed tests to review")
    except Exception:  # noqa: BLE001 - informational line
        pass
    if ai:
        lines.append(f"\U0001F916 {bold('AI')}: " + "; ".join(ai))
    rs = [r for r in research if r]
    if rs:
        lines += ["", f"\U0001F52C {bold('RESEARCH')}"] + rs
    final: dict[tuple[str, str], dict] = {}
    for f in fills:                                   # several fills per symbol: the last one is the position held
        final[(f["strategy"], f["symbol"])] = f
    if final:
        by: dict[str, list[str]] = {}
        for (strat, sym), f in final.items():
            w = f["target"]
            by.setdefault(name(strat), []).append(
                f"{sym} {'flat' if abs(w) < 1e-6 else ('long' if w > 0 else 'short') + f' {abs(w):.0%}'} @{f['price']:.2f}")
        lines += ["", f"\U0001F9FE {bold('Filled at close')}: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in by.items())]

    if broker:
        lines += ["", f"\U0001F3E6 {broker}"]
    return clean("\n".join(lines))


def parts(text: str, limit: int = 1490) -> list[str]:
    """Split at line breaks into parts of at most ``limit`` UTF-16 units, marked (i/n). Nothing is dropped (Sms.send
    truncates at SMS_MAX, which cut the 11-09 digest); an over-long line is hard-wrapped. NEEDS YOU is in part 1."""
    chunks: list[str] = []
    cur = ""
    for ln in text.split("\n"):
        while units(ln) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            cut = limit
            while units(ln[:cut]) > limit:               # styled letters / emoji are 2 units each
                cut -= 1
            chunks.append(ln[:cut])
            ln = ln[cut:]
        cand = f"{cur}\n{ln}" if cur else ln
        if units(cand) > limit and cur:
            chunks.append(cur)
            cur = ln
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    if len(chunks) <= 1:
        return chunks
    return [f"({i}/{len(chunks)}) {c}" for i, c in enumerate(chunks, 1)]
