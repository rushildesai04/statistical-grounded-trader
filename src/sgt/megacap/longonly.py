"""Forward ledger of the daily model's LONG calls only (docs/findings/prereg_megacap_long_forward_2026-11-13.md).

No model of its own: the +1 positions of the two forward sets' marks. Pure and bounded; a missing or unreadable mark
file gives an empty ledger (the caller shows nothing), never a partial one."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

START = "2026-11-16"                                # first long-only session (prereg)
STOP = -0.05                                        # prereg stop rule: cumulative net below -5%
TESTED_AVG, TESTED_WIN = 0.0023, 0.538              # 2018-2026 out of sample, 4,724 long calls (motivation only)
SETS = ("megacap_forward/marks.csv", "megacap_ext/marks.csv")
MAX_BYTES = 5_000_000
# The same ledger on the model's 2018-2026 out-of-sample record, day by day (scripts/build_long_ledger_history.py).
HISTORY = Path(__file__).resolve().parents[3] / "config" / "models" / "long_ledger_history.json"
RARITY_MIN_SESSIONS, RARITY_MIN_WINDOWS = 3, 250


def _marks(p: Path) -> dict[str, list[tuple[int, float, float]]] | None:
    """date -> [(position, ret, net)] for unvoided rows; None when the file is missing or unreadable."""
    try:
        if not p.exists() or p.stat().st_size > MAX_BYTES:
            return None
        out: dict[str, list[tuple[int, float, float]]] = {}
        with p.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r["void"] == "True":
                    continue
                out.setdefault(r["date"], []).append((int(float(r["position"])), float(r["ret"]), float(r["net"])))
        return out
    except (OSError, KeyError, ValueError, csv.Error):
        return None


def ledger(root: Path, start: str = START) -> dict:
    """Sessions >= ``start`` marked in BOTH sets. Per name: net when position = +1, else 0; day = mean over names;
    days compound. Also the same sessions for the long-and-short models and for holding the names."""
    sets = [_marks(root / s) for s in SETS]
    if any(s is None for s in sets):
        return {"sessions": 0}
    days = sorted(d for d in set(sets[0]) & set(sets[1]) if d >= start)
    long_v = full_v = hold_v = 1.0
    calls: list[float] = []
    for d in days:
        rows = sets[0][d] + sets[1][d]
        longs = [net for pos, _, net in rows if pos == 1]
        calls += longs
        long_v *= 1 + sum(longs) / len(rows)
        full_v *= 1 + sum(net for _, _, net in rows) / len(rows)
        hold_v *= 1 + sum(ret for _, ret, _ in rows) / len(rows)
    out = {"sessions": len(days), "calls": len(calls), "wins": sum(x > 0 for x in calls)}
    if days:
        out.update(since=days[0], last=days[-1], total=long_v - 1, long_short=full_v - 1, holding=hold_v - 1,
                   avg_call=sum(calls) / len(calls) if calls else None, stop=long_v - 1 < STOP)
    return out


def history(p: Path = HISTORY) -> list[float] | None:
    """The tested record's daily ledger returns; None when the file is missing, too large or not plain numbers."""
    try:
        if not p.exists() or p.stat().st_size > MAX_BYTES:
            return None
        days = [float(x) for x in json.loads(p.read_text())["days"]]
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return days if days and all(math.isfinite(x) and -1 < x < 1 for x in days) else None


def rarity(total: float, sessions: int, days: list[float] | None) -> float | None:
    """Share of the tested record's stretches of ``sessions`` consecutive days that compounded to ``total`` or less
    (red-day review 2026-11-20: how unusual is the live ledger, said the same way every night). Description only: the
    registered stop and decision date decide. None = too few sessions or too short a record to say."""
    if not days or sessions < RARITY_MIN_SESSIONS or len(days) - sessions + 1 < RARITY_MIN_WINDOWS or not math.isfinite(total):
        return None
    cum = [0.0]
    for x in days:
        cum.append(cum[-1] + math.log1p(x))
    lim = math.log1p(total) + 1e-12
    n = len(days) - sessions + 1
    return sum(cum[i + sessions] - cum[i] <= lim for i in range(n)) / n
