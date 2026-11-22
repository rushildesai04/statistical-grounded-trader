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
# The registered review of each book (exec_baseline_2026-11-21.md, section 6; fixed 2026-11-21, before any trade of the
# sample existed). Only trades from REVIEW_FROM count, only those labelled MODELED (no exclusion flag), in the order
# they were made; the review reads the FIRST ``REVIEW_N`` of them, never a later or a hand-picked window.
REVIEW_FROM = "2026-11-23"
REVIEW_N = {"short": 50, "long": 30}        # the review point
EARLY = {"short": (25, -0.05), "long": (15, -0.03)}     # the one early look: (trades, pause if their average is under)
RETIRE_AT = {"short": -0.02, "long": 0.0}   # retire at an average at or under this
KEEP_BOUND = {"short": -0.02, "long": -0.01}            # keep needs an average above 0 AND its lower bound above this
EXTEND_N = {"short": 100, "long": 60}       # an inconclusive review is extended once, to this many trades
EXTEND_UNTIL = "2027-02-26"                 # ... or to this date, whichever comes first; then the review is final
MIN_FINAL_N = 10                            # a book with fewer trades at the final date cannot be judged
BOOT_N, BOOT_SEED, BOUND_Q = 10_000, 20261123, 0.10     # lower bound: 10th percentile of session-resampled means


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


def summary(path: Path = LEDGER, since: str | None = None) -> dict:
    """Per book: trades in the ledger (sessions from ``since``), the MODELED ones (the baseline), their average under
    the model and in the original record, the stress average (stops at their minute's extreme), and how many are
    UNRESOLVED."""
    out = {}
    for book in BOOKS:
        rs = [r for r in rows(path) if r["book"] == book and (since is None or str(r.get("session") or "") >= since)]
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


def _bound(sessions: list[str], nets: list[float]) -> float | None:
    """Lower bound of the average: the 10th percentile of the means of BOOT_N resamples of whole SESSIONS (trades of
    one day move together, so days are resampled, not trades). None with fewer than two sessions."""
    import numpy as np

    u, idx = np.unique(np.array(sessions), return_inverse=True)
    if len(u) < 2:
        return None
    s, n = np.bincount(idx, np.array(nets, dtype=float), len(u)), np.bincount(idx, minlength=len(u)).astype(float)
    pick = np.random.default_rng(BOOT_SEED).integers(0, len(u), size=(BOOT_N, len(u)))
    return float(np.quantile(s[pick].sum(1) / n[pick].sum(1), BOUND_Q))


def _verdict(book: str, sample: list[tuple[str, float]], final: bool) -> dict:
    nets = [x for _, x in sample]
    mean, low = sum(nets) / len(nets), _bound([d for d, _ in sample], nets)
    if mean <= RETIRE_AT[book]:
        state = "RETIRE"
    elif mean > 0 and low is not None and low > KEEP_BOUND[book]:
        state = "KEEP (paper; never by itself a reason for real money)"
    else:
        state = "NOT ESTABLISHED: the review is final, the book stays paper-only or ends (owner)" if final else \
            "INCONCLUSIVE: remains paper-only, extended once"
    return {"state": state, "n": len(nets), "mean": mean, "lower": low, "sessions": len({d for d, _ in sample})}


def review(book: str, path: Path = LEDGER, today: str | None = None) -> dict:
    """Where ``book`` stands against its registered criteria. ``today`` (ISO date) only matters after EXTEND_UNTIL."""
    sample = sorted(((str(r.get("session")), r["id"], _num((r.get("model") or {}).get("net"))) for r in rows(path)
                     if r["book"] == book and r.get("label") == "MODELED" and str(r.get("session") or "") >= REVIEW_FROM),
                    key=lambda x: (x[0], str(x[1])))
    sample = [(d, x) for d, _, x in sample if x is not None]
    n, late = len(sample), bool(today and today > EXTEND_UNTIL)
    if n >= REVIEW_N[book]:
        first = _verdict(book, sample[:REVIEW_N[book]], final=False)
        if first["state"].startswith("INCONCLUSIVE") and (n >= EXTEND_N[book] or late):
            return _verdict(book, sample[:EXTEND_N[book]], final=True)
        return first
    if late:
        return _verdict(book, sample, final=True) if n >= MIN_FINAL_N else \
            {"state": "NOT ESTABLISHED: too few trades by the final date", "n": n, "mean": None, "lower": None, "sessions": None}
    en, under = EARLY[book]
    early = n >= en and sum(x for _, x in sample[:en]) / en < under
    return {"state": "EARLY LOOK MET: pause for a decision (owner)" if early else "collecting", "n": n,
            "mean": sum(x for _, x in sample) / n if n else None, "lower": None, "sessions": len({d for d, _ in sample})}


def line(path: Path = LEDGER, today: str | None = None) -> str | None:
    """One line for the nightly summary: the forward sample only (trades from REVIEW_FROM); None while it is empty."""
    s, parts = summary(path, REVIEW_FROM), []
    for book, name in BOOKS.items():
        b = s[book]
        if not b["trades"]:
            continue
        v = review(book, path, today)
        orig = f" (original record {b['original']:+.1%})" if b["original"] is not None else ""
        avg = f"{b['model']:+.1%} a trade at real quotes{orig}, {b['stress']:+.1%} with stops at the worst price of their " \
              "minute" if b["modeled"] else "none priced yet"
        parts.append(f"{name} {b['modeled']} of {REVIEW_N[book]} with no exclusion flag: {avg}; {b['unresolved']} flagged "
                     f"(no listed shares or price test) and not counted; review: {v['state']}")
    return f"executable baseline since {REVIEW_FROM[5:]} (a model, not fills; a research ledger, not stop protection): " \
        + "; ".join(parts) if parts else None
