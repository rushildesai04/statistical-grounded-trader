"""Do the long book's high-cost trades earn their cost? (docs/findings/prereg_long_cost_cap_2026-11-20.md)

    .venv/bin/python scripts/long_cost_cap_study.py HISTORY.csv [NOVEMBER.csv]      # run ONCE (frozen prereg)

The inputs are the bot replay's rows (scripts/long_bot_replay.py) at the live configuration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trader_rules_long_study import boot_groups  # noqa: E402  (seed 20261112, 10,000 resamples)

OUT = Path("reports/long_cost_cap")
CAP, MIN_N, CFG = 0.01, 30, "new4"


def trades(path: Path, split: str | None) -> pd.DataFrame:
    """Settled trades of the live configuration in ``path`` (one split of the history, or every row of a file that
    has no history split), with a result and a cost."""
    t = pd.read_csv(path, converters={"symbol": str})            # the ticker NA is a ticker
    t = t[(t.cfg == CFG) & (t.status == "settled")]
    t = t[t.split == split] if split else t
    return t.dropna(subset=["net", "cost"])


def stats(t: pd.DataFrame) -> dict:
    """Trades at or under the cap against trades above it."""
    kept, skip = t[t.cost <= CAP], t[t.cost > CAP]
    out = {"n_kept": len(kept), "n_skipped": len(skip), "all": float(t.net.mean()) if len(t) else None,
           "kept": float(kept.net.mean()) if len(kept) else None, "skipped": float(skip.net.mean()) if len(skip) else None,
           "kept_gross": float(kept.gross.mean()) if len(kept) else None,
           "skipped_gross": float(skip.gross.mean()) if len(skip) else None}
    if len(kept) and len(skip):
        dist = np.asarray(boot_groups(kept.session.to_numpy(), kept.net.to_numpy(float), skip.session.to_numpy(),
                                      skip.net.to_numpy(float)), float)
        out.update(diff=out["kept"] - out["skipped"], p=float(((dist <= 0) | np.isnan(dist)).mean()))
    return out


def passed(st: dict) -> bool:
    return bool(st["n_skipped"] >= MIN_N and (st.get("skipped") or 0) < 0 and (st.get("diff") or 0) > 0)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    res = OUT / "result.json"
    if res.is_file():
        print(f"{res} exists: the run was made once and is not repeated")
        return 2
    if not argv:
        print(__doc__)
        return 1
    hold = stats(trades(Path(argv[0]), "holdout"))
    out = {"prereg": "docs/findings/prereg_long_cost_cap_2026-11-20.md", "cap": CAP, "holdout": hold, "pass": passed(hold),
           "training_disclosed": stats(trades(Path(argv[0]), "train")),
           "november_reported": stats(trades(Path(argv[1]), None)) if len(argv) > 1 else None}
    OUT.mkdir(parents=True, exist_ok=True)
    res.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
