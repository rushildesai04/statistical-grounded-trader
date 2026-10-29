"""Score the pre-registered short-side rule on the lockbox, once.

Implements docs/findings/prereg_short_side_2026-10-29.md exactly; every run is logged in the
trial ledger (kind="short-lockbox").

Usage:
    python scripts/short_lockbox_test.py
"""
from __future__ import annotations

import time

import httpx
import numpy as np
import pandas as pd

from sgt.config import get_settings
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.decision.models import DecisionModel
from sgt.decision.validation import cluster_bootstrap_ci, labeled_rows
from sgt.intraday.sources import _alpaca_headers

DATASET = "data/decision/gappers_5y/dataset.parquet"
LOCKBOX = "2025-10-01"
LABEL, WEIGHT = "y_net__tp2_sl2_h15", "uniq_w__tp2_sl2_h15"


def select(rows: pd.DataFrame, model: DecisionModel, cut: float) -> pd.DataFrame:
    x = labeled_rows(rows, LABEL).copy()
    x["mu"] = model.predict_frame(x)["mu"]
    x = x[(x["mu"] <= cut) & (np.exp(x["f_log_price"]) >= 5.0)]
    x = x.sort_values("ts").groupby(["symbol", "session_date"]).head(1).copy()
    x["short_net"] = -(x[LABEL] + x["cost_rt_label"]) - x["cost_rt_label"]
    return x


def summarize(name: str, x: pd.DataFrame) -> dict:
    if len(x) < 2:
        print(f"{name}: n={len(x)}")
        return {"n": len(x), "mean": float("nan"), "lo": float("nan"), "hi": float("nan")}
    lo, hi = cluster_bootstrap_ci(x["short_net"].to_numpy(), x["session_date"].to_numpy(), alpha=0.10)
    top5 = x["short_net"].nlargest(5).sum() / x["short_net"][x["short_net"] > 0].sum()
    print(f"{name}: n={len(x)} mean={100 * x.short_net.mean():+.3f}% median={100 * x.short_net.median():+.3f}% "
          f"hit={(x.short_net > 0).mean():.0%} 90%CI[{100 * lo:+.3f}%, {100 * hi:+.3f}%] "
          f"worst={100 * x.short_net.min():+.2f}% top5-share={top5:.0%} days={x.session_date.nunique()}")
    return {"n": len(x), "mean": float(x.short_net.mean()), "lo": lo, "hi": hi}


def shortability(symbols: list[str]) -> pd.DataFrame:
    url = get_settings().alpaca_paper_url.rstrip("/") + "/v2/assets/"
    out = []
    with httpx.Client(timeout=get_settings().http_timeout_s) as c:
        for s in symbols:
            r = c.get(url + s, headers=_alpaca_headers())
            j = r.json() if r.status_code == 200 else {}
            out.append({"symbol": s, "status": j.get("status", "unknown"),
                        "shortable": bool(j.get("shortable", False)),
                        "easy_to_borrow": bool(j.get("easy_to_borrow", False))})
            time.sleep(0.31)
    return pd.DataFrame(out)


def main() -> int:
    df = pd.read_parquet(DATASET)
    train = df[df["session_date"] < LOCKBOX]
    train = train.sample(frac=1.0, random_state=0).groupby(["symbol", "session_date"], sort=False).head(10)
    model = DecisionModel().fit(labeled_rows(train, LABEL), [c for c in df.columns if c.startswith("f_")],
                                LABEL, weight_col=WEIGHT, seed=0)
    cut = float(np.quantile(model.predict_frame(labeled_rows(train, LABEL))["mu"], 0.10))
    lock = df[df["session_date"] >= LOCKBOX]
    print(f"threshold mu <= {cut:.5f}; lockbox sessions {lock.session_date.min()}..{lock.session_date.max()}")

    primary = select(lock, model, cut)
    res = summarize("PRIMARY (uncapped)", primary)
    capped = lock.sample(frac=1.0, random_state=0).groupby(["symbol", "session_date"], sort=False).head(10)
    summarize("secondary (capped like training)", select(capped, model, cut))

    verdict = ("PASS" if res["n"] >= 50 and res["mean"] > 0 and res["lo"] > 0 else
               "INCONCLUSIVE" if res["n"] >= 50 and res["mean"] > 0 else "FAIL")
    print(f"VERDICT: {verdict}")

    sh = shortability(sorted(primary["symbol"].unique()))
    m = primary.merge(sh, on="symbol", how="left")
    print(f"today's flags: {sh.easy_to_borrow.mean():.0%} of symbols easy-to-borrow, "
          f"{sh.shortable.mean():.0%} shortable, {(sh.status != 'active').mean():.0%} inactive/delisted")
    summarize("secondary (easy-to-borrow today)", m[m["easy_to_borrow"]])
    primary.merge(sh, on="symbol", how="left").to_csv("reports/decision/short_lockbox_trades.csv", index=False)

    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="short-lockbox-2026-10-29", kind="short-lockbox",
                                  params={"prereg": "docs/findings/prereg_short_side_2026-10-29.md"},
                                  metrics={**res, "verdict": verdict})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
