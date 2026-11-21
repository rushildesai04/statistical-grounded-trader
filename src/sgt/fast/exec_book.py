"""The reconciled ledger on disk: each paper trade as the execution model prices it, next to its original result.

``scripts/exec_ledger.py --append`` writes one JSON line per trade to ``reports/exec_model/ledger.jsonl`` every night
(model: docs/findings/exec_model_2026-11-21.md). This module only READS that file: the nightly summary line and the
continue / pause / retire read of each experimental book (docs/findings/exec_baseline_2026-11-21.md). The original
ledger in the ops database is never changed; a missing or damaged file means "no line", never a number.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

LEDGER = Path("reports/exec_model/ledger.jsonl")
MAX_BYTES = 20_000_000                      # about 1 kB a trade: years of trades; bounds a bad file
BOOKS = {"short": "shorts", "long": "longs"}
REVIEW_N = {"short": 100, "long": 60}       # modeled trades at which a book is put to the owner (exec_baseline, section 6)


def rows(path: Path = LEDGER) -> list[dict]:
    """Every readable line, the LAST one per (book, id) winning; malformed lines are skipped."""
    try:
        if not path.exists() or path.stat().st_size > MAX_BYTES:
            return []
        out: dict[tuple, dict] = {}
        with path.open() as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("book") in BOOKS and r.get("id") is not None:
                    out[(r["book"], r["id"])] = r
        return list(out.values())
    except OSError:
        return []


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def summary(path: Path = LEDGER) -> dict:
    """Per book: trades in the ledger, the MODELED ones (the baseline), their average under the model and in the
    original record, the stress average (stops at their minute's extreme), and how many are UNRESOLVED."""
    out = {}
    for book in BOOKS:
        rs = [r for r in rows(path) if r["book"] == book]
        mod = [(m, o, sx) for r in rs if r.get("label") == "MODELED"
               for m, o, sx in [(_num((r.get("model") or {}).get("net")), _num((r.get("orig") or {}).get("net")),
                                 _num((r.get("model") or {}).get("stress_extreme_net")))] if m is not None]
        n = len(mod)
        se = math.sqrt(sum((m - sum(x[0] for x in mod) / n) ** 2 for m, _, _ in mod) / (n - 1) / n) if n > 1 else None
        both = [(m, o) for m, o, _ in mod if o is not None]
        out[book] = {"trades": len(rs), "modeled": n, "unresolved": sum(r.get("label") == "UNRESOLVED" for r in rs),
                     "model": sum(m for m, _, _ in mod) / n if n else None, "se": se,
                     "original": sum(o for _, o in both) / len(both) if both else None,
                     "stress": sum(sx if sx is not None else m for m, _, sx in mod) / n if n else None}
    return out


def line(path: Path = LEDGER) -> str | None:
    """One line for the nightly summary; None while the ledger is empty."""
    s, parts = summary(path), []
    for book, name in BOOKS.items():
        b = s[book]
        if not b["modeled"]:
            continue
        orig = f" (original record {b['original']:+.1%})" if b["original"] is not None else ""
        due = " REVIEW DUE" if b["modeled"] >= REVIEW_N[book] else f" of {REVIEW_N[book]}"
        parts.append(f"{name} {b['modeled']}{due}: {b['model']:+.1%} a trade at real quotes{orig}, {b['stress']:+.1%} with "
                     f"stops at the worst price of their minute; {b['unresolved']} could not be priced or placed")
    return "executable baseline (a model, not fills): " + "; ".join(parts) if parts else None
