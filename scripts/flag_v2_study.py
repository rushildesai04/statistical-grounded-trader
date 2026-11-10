"""The bull flag trade with detector v2 (prereg_flag_v2_2026-11-10).

    python scripts/flag_v2_study.py      # run ONCE
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_family_study import Bars, fade_events  # noqa: E402
from long_setups_study import LOCATE, SECONDARY, boot, holm, latest  # noqa: E402

from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.fast import patterns as pt  # noqa: E402

OUT, D_STAR, MIN_N, HOLDOUT_MIN_N, ALPHA = Path("reports/flag_v2"), "2026-07-01", 50, 20, 0.05


def main() -> int:
    bars, rows = Bars(), []
    fe = fade_events().sort_values(["symbol", "session"]).reset_index(drop=True)
    for r in fe.itertuples():
        f = bars(r.symbol, r.session)
        if not len(f):
            continue
        for s in pt.bull_flag_v2(f, r.t0):
            row = {"symbol": r.symbol, "session": r.session, "kind": s.kind}
            t = pt.simulate(f, s, latest=latest(r.session))
            if t is not None:
                fee = r.cost_rt + (LOCATE if s.side == "short" else 0.0)
                row.update(net=t.gross - fee, gross=t.gross, reason=t.reason)
                for k, kw in SECONDARY.items():
                    t2 = pt.simulate(f, s, latest=latest(r.session), **kw)
                    row[f"net_{k}"] = None if t2 is None else t2.gross - fee
            rows.append(row)
    t = pd.DataFrame(rows)
    t["split"] = np.where(t.session < D_STAR, "train", "holdout")
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "trades.csv", index=False)
    out, ps = {}, {}
    for k in ("L5v2", "S2v2"):
        d = t[(t.kind == k) & (t.split == "train")].dropna(subset=["net"])
        m = boot(d) if len(d) else np.array([0.0])
        out[k] = {"flags": int(((t.kind == k) & (t.split == "train")).sum()), "n": len(d),
                  "mean": float(d.net.mean()) if len(d) else None, "gross": float(d.gross.mean()) if len(d) else None,
                  "win": float((d.net > 0).mean()) if len(d) else None, "p": float((m <= 0).mean()),
                  "secondary": {c: float(d[f"net_{c}"].mean()) for c in SECONDARY} if len(d) else {}}
        ps[k] = out[k]["p"] if len(d) >= MIN_N else 1.0
    passed = holm(ps, ALPHA)
    for k in ("L5v2", "S2v2"):
        out[k]["pass"] = bool(passed[k])
        if passed[k]:
            h = t[(t.kind == k) & (t.split == "holdout")].dropna(subset=["net"])
            hm = boot(h) if len(h) else np.array([0.0])
            out[k]["holdout"] = {"n": len(h), "mean": float(h.net.mean()) if len(h) else None,
                                 "p": float((hm <= 0).mean()),
                                 "confirmed": bool(len(h) >= HOLDOUT_MIN_N and h.net.mean() > 0 and (hm <= 0).mean() < 0.05)}
    print(json.dumps(out, indent=1))
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="flag-v2-2026-11-10", kind="flag-v2",
                                  params={"prereg": "docs/findings/prereg_flag_v2_2026-11-10.md"}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
