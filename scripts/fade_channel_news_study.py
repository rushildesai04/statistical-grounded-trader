"""The channel's own news alert after the alert we short (docs/findings/prereg_fade_channel_news_2026-11-20.md)

    .venv/bin/python scripts/fade_channel_news_study.py      # run ONCE (frozen prereg)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trader_rules_long_study import boot_groups  # noqa: E402  (seed 20261112, 10,000 resamples)

OUT = Path("reports/fade_channel_news")
ALPHA, MIN_N, HOLD = 0.05, 30, pd.Timedelta(minutes=60)
CONV = {"symbol": str}                                    # the ticker NA is a ticker


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(no-news fades with their tested entry bar, the channel's news alerts)."""
    ev = pd.concat([pd.read_csv(f"reports/fade_news/{n}_events.csv", converters=CONV).assign(split=n)
                    for n in ("dev", "holdout")])
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv", converters=CONV)[["symbol", "session", "entry_ts"]]
    ev = ev.assign(session=ev.session.astype(str)).merge(src.assign(session=src.session.astype(str)), on=["symbol", "session"])
    news = pd.read_csv("reports/telegram/trades_news.csv", converters=CONV)[["symbol", "session", "posted_at"]]
    return ev[(ev.news_own == False) & (ev.alert_type != "news")], news.assign(session=news.session.astype(str))  # noqa: E712


def flag(fades: pd.DataFrame, news: pd.DataFrame) -> pd.DataFrame:
    """``pre``: a news alert for the same stock and session after the fade's alert and before its entry bar;
    ``hold``: one from the entry bar to the end of the hold (and none before); ``flagged``: either."""
    f = fades.assign(posted=pd.to_datetime(fades.posted_at, utc=True), entry=pd.to_datetime(fades.entry_ts, utc=True))
    n = news.assign(npost=pd.to_datetime(news.posted_at, utc=True))[["symbol", "session", "npost"]]
    m = f[["symbol", "session", "posted", "entry"]].merge(n, on=["symbol", "session"], how="left")
    m = m.assign(pre=(m.npost > m.posted) & (m.npost < m.entry), late=(m.npost >= m.entry) & (m.npost < m.entry + HOLD))
    g = m.groupby(["symbol", "session"])[["pre", "late"]].any().reset_index()
    f = f.merge(g, on=["symbol", "session"], how="left")
    return f.assign(hold=f.late & ~f.pre, flagged=f.pre | f.late)


def stats(b: pd.DataFrame) -> dict:
    b = b.dropna(subset=["net", "entry"])
    fl, un, pre = b[b.flagged], b[~b.flagged], b[b.pre]
    mean = lambda q: float(q.net.mean()) if len(q) else None  # noqa: E731
    out = {"n_flagged": len(fl), "n_unflagged": len(un), "n_pre": len(pre), "n_hold": int(b.hold.sum()),
           "flagged": mean(fl), "unflagged": mean(un), "pre": mean(pre), "hold": mean(b[b.hold]),
           "flagged_wins": float((fl.net > 0).mean()) if len(fl) else None}
    if len(fl) and len(un):
        dist = np.asarray(boot_groups(fl.session.to_numpy(), fl.net.to_numpy(float), un.session.to_numpy(),
                                      un.net.to_numpy(float)), float)
        out.update(diff=out["flagged"] - out["unflagged"], p=float(((dist >= 0) | np.isnan(dist)).mean()))
    return out


def passed(st: dict) -> bool:
    return bool(st["n_flagged"] >= MIN_N and st.get("diff") is not None and st["diff"] < 0 and st["p"] < ALPHA
                and st.get("pre") is not None and st["pre"] < st["unflagged"])


def main() -> int:
    res = OUT / "result.json"
    if res.is_file():
        print(f"{res} exists: the run was made once and is not repeated")
        return 2
    d = flag(*load())
    hold = stats(d[d.split == "holdout"])
    out = {"prereg": "docs/findings/prereg_fade_channel_news_2026-11-20.md", "holdout": hold, "pass": passed(hold),
           "training_disclosed": stats(d[d.split == "dev"])}
    OUT.mkdir(parents=True, exist_ok=True)
    res.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
