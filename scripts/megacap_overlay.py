"""Daily model as a timing overlay on holding (prereg_megacap_overlay_2026-11-06).

    python scripts/megacap_overlay.py history   # secondary: the 2018-2026 out-of-sample record, all 21 names
    python scripts/megacap_overlay.py shadow    # nightly on the runner: live marks -> reports/megacap_overlay
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

COST_RT = 0.0004
MARKS = Path("reports/megacap_forward/marks.csv")
OUT = Path("reports/megacap_overlay")
HISTORY = (Path("reports/megacap_daily"), Path("reports/megacap_oos"), Path("reports/aaoi_daily"))


def overlay(position: pd.Series, ret: pd.Series) -> pd.Series:
    """Hold unless the model says short: then cash for the day, paying one round trip."""
    return pd.Series(np.where(position < 0, -COST_RT, ret), index=ret.index)


def max_dd(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    return float((eq / eq.cummax() - 1).min())


def summary(hold: pd.Series, over: pd.Series) -> dict:
    def sh(x: pd.Series) -> float | None:
        return float(x.mean() / x.std() * math.sqrt(252)) if x.std() > 0 else None
    return {"days": len(hold), "hold_ann": float(hold.mean() * 252), "overlay_ann": float(over.mean() * 252),
            "hold_sharpe": sh(hold), "overlay_sharpe": sh(over), "hold_maxdd": max_dd(hold),
            "overlay_maxdd": max_dd(over), "aside_share": float((over != hold).mean())}


def cmd_history() -> int:
    rows = {}
    for root in HISTORY:
        for f in sorted(root.glob("oos_*.parquet")):
            t = pd.read_parquet(f).dropna(subset=["label", "position"])
            rows[f.stem[4:]] = summary(t.label, overlay(t.position, t.label))
    print(f"{'stock':6s} {'hold/yr':>8s} {'overlay/yr':>10s} {'hold Shp':>8s} {'ovl Shp':>8s} {'hold DD':>8s} "
          f"{'ovl DD':>8s} {'aside':>6s}")
    for s, r in sorted(rows.items(), key=lambda kv: -((kv[1]["overlay_sharpe"] or 0) - (kv[1]["hold_sharpe"] or 0))):
        print(f"{s:6s} {r['hold_ann']:+8.1%} {r['overlay_ann']:+10.1%} {r['hold_sharpe']:8.2f} {r['overlay_sharpe']:8.2f} "
              f"{r['hold_maxdd']:+8.1%} {r['overlay_maxdd']:+8.1%} {r['aside_share']:6.0%}")
    better = sum((r["overlay_sharpe"] or 0) > (r["hold_sharpe"] or 0) for r in rows.values())
    print(f"overlay Sharpe above hold on {better} of {len(rows)} names")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "history.json").write_text(json.dumps(rows, indent=2))
    return 0


def cmd_shadow() -> int:
    if not MARKS.exists():
        print("no live marks yet")
        return 0
    m = pd.read_csv(MARKS)
    m = m[~m.void.astype(bool)] if "void" in m else m
    t = pd.DataFrame({"session": m.date.astype(str), "symbol": m.symbol, "net_champion": m.ret,
                      "net_overlay": overlay(m.position, m.ret)})
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "shadow.csv", index=False)
    line = (f"megacap overlay fwd n={len(t)} symbol-days: hold {t.net_champion.mean():+.2%}/day, overlay "
            f"{t.net_overlay.mean():+.2%}/day, stepped aside {(m.position < 0).mean():.0%} of days")
    (OUT / "shadow_summary.json").write_text(json.dumps({"n": len(t), "line": line}))
    print(line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["history", "shadow"])
    return {"history": cmd_history, "shadow": cmd_shadow}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
