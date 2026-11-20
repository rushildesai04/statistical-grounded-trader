"""Forward ledger of the daily model's long calls made on a DOWN day (docs/findings/prereg_megacap_downday_forward_2026-11-19.md).

A second ledger beside the registered long-calls ledger (``sgt.megacap.longonly``, untouched): the same mark files,
the same arithmetic, on the subset of +1 calls whose stock was down 1% or more by the decision. Pure and bounded; a
session whose inputs cannot all be read is left out whole, never counted in part."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

START = "2026-11-20"                    # the first session after the owner's word (2026-11-19)
DOWN = -0.01                            # a down day: the stock's move to the decision is below this
DECIDE = "2027-04-30"                   # the registered long-calls book's own decision date
SETS = ("megacap_forward", "megacap_ext")
MAX_BYTES = 5_000_000


def _marks(p: Path) -> dict[str, list[tuple[str, int, float]]] | None:
    """date -> [(symbol, position, net)] for unvoided rows; None when the file is missing or unreadable."""
    try:
        if not p.exists() or p.stat().st_size > MAX_BYTES:
            return None
        out: dict[str, list[tuple[str, int, float]]] = {}
        with p.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r["void"] == "True":
                    continue
                out.setdefault(r["date"], []).append((r["symbol"], int(float(r["position"])), float(r["net"])))
        return out
    except (OSError, KeyError, ValueError, csv.Error):
        return None


def moves(p: Path) -> dict[str, float] | None:
    """symbol -> the stock's move on the decision day as the decision file stored it at 15:50 ET
    (``exp(t_gap + t_r_intraday) - 1``). None when the file is missing, too big or not the expected shape; a symbol
    whose two numbers are missing or not finite is simply absent."""
    try:
        if not p.is_file() or p.stat().st_size > MAX_BYTES:
            return None
        pos = json.loads(p.read_text()).get("positions")
        if not isinstance(pos, dict):
            return None
        out = {}
        for sym, v in pos.items():
            f = v.get("features") if isinstance(v, dict) else None
            g, r = (f.get("t_gap"), f.get("t_r_intraday")) if isinstance(f, dict) else (None, None)
            if all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (g, r)):
                out[str(sym)] = math.expm1(g + r)
        return out
    except (OSError, ValueError, AttributeError):
        return None


def ledger(root: Path, start: str = START) -> dict:
    """Sessions >= ``start`` marked in BOTH sets whose decision files are both readable. Per name: net for a down-day
    long call, else 0; day = mean over names; days compound. Beside it, ALL long calls on the same sessions."""
    sets = [_marks(root / s / "marks.csv") for s in SETS]
    if any(s is None for s in sets):
        return {"sessions": 0}
    down_v = all_v = 1.0
    calls: list[float] = []
    every: list[float] = []
    used, skipped = [], 0
    for d in sorted(x for x in set(sets[0]) & set(sets[1]) if x >= start):
        mv = [moves(root / s / "decisions" / f"{d}.json") for s in SETS]
        rows = [(sym, pos, net, m) for marks, m in zip(sets, mv) for sym, pos, net in marks[d]]
        if any(m is None for m in mv) or any(pos == 1 and sym not in m for sym, pos, _, m in rows):
            skipped += 1                # a long call whose move cannot be read: the session is left out whole
            continue
        longs = [net for _, pos, net, _ in rows if pos == 1]
        downs = [net for sym, pos, net, m in rows if pos == 1 and m[sym] < DOWN]
        calls += downs
        every += longs
        down_v *= 1 + sum(downs) / len(rows)
        all_v *= 1 + sum(longs) / len(rows)
        used.append(d)
    out = {"sessions": len(used), "skipped": skipped, "calls": len(calls), "wins": sum(x > 0 for x in calls),
           "all_calls": len(every)}
    if used:
        out.update(since=used[0], last=used[-1], total=down_v - 1, all_total=all_v - 1,
                   avg_call=sum(calls) / len(calls) if calls else None,
                   all_avg=sum(every) / len(every) if every else None)
    return out


def line(root: Path) -> str | None:
    """One line for the nightly summary; None before the first session. Never raises."""
    try:
        r = ledger(root)
        if not r["sessions"]:
            return None
        avg = f"{r['avg_call']:+.2%}" if r["avg_call"] is not None else "none yet"
        allavg = f"{r['all_avg']:+.2%}" if r["all_avg"] is not None else "none yet"
        return (f"down-day long calls (record only), {r['sessions']} sessions since {r['since'][5:]}: {r['total']:+.1%} "
                f"({r['calls']} calls, {r['wins']} winners, avg {avg}); all long calls same days {r['all_total']:+.1%} "
                f"({r['all_calls']} calls, avg {allavg}); decided {DECIDE}"
                + (f"; {r['skipped']} sessions left out (inputs unreadable)" if r["skipped"] else ""))
    except Exception:  # noqa: BLE001 - an informational line
        return None
