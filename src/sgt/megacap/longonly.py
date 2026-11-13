"""Forward ledger of the daily model's LONG calls only (docs/findings/prereg_megacap_long_forward_2026-11-13.md).

No model of its own: the +1 positions of the two forward sets' marks. Pure and bounded; a missing or unreadable mark
file gives an empty ledger (the caller shows nothing), never a partial one."""
from __future__ import annotations

import csv
from pathlib import Path

START = "2026-11-16"                                # first long-only session (prereg)
STOP = -0.05                                        # prereg stop rule: cumulative net below -5%
TESTED_AVG, TESTED_WIN = 0.0023, 0.538              # 2018-2026 out of sample, 4,724 long calls (motivation only)
SETS = ("megacap_forward/marks.csv", "megacap_ext/marks.csv")
MAX_BYTES = 5_000_000


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
