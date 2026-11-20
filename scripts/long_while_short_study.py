"""No long while our own short on the stock is open (docs/findings/prereg_long_while_short_2026-11-20.md)

    .venv/bin/python scripts/long_while_short_study.py HISTORY.csv      # run ONCE (frozen prereg)

HISTORY.csv = the bot replay's rows (scripts/long_bot_replay.py) at the live configuration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fade_repeat_days_study import mark_repeats  # noqa: E402
from trader_rules_long_study import boot_groups  # noqa: E402  (seed 20261112, 10,000 resamples)

OUT = Path("reports/long_while_short")
MIN_N, CFG, HOLD = 30, "new4", pd.Timedelta(minutes=60)
CONV = {"symbol": str}                                    # the ticker NA is a ticker


def alerts() -> pd.DataFrame:
    """One row a stock-day: does the short book take the alert by its tested rule, and its tested entry bar."""
    ev = pd.concat([pd.read_csv(f"reports/fade_news/{n}_events.csv", converters=CONV) for n in ("dev", "holdout")])
    ev = mark_repeats(ev.assign(session=ev.session.astype(str))).drop_duplicates(["symbol", "session"])
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv", converters=CONV)[["symbol", "session", "entry_ts"]]
    ev = ev.merge(src.assign(session=src.session.astype(str)).rename(columns={"entry_ts": "short_ts"}), on=["symbol", "session"])
    taken = (ev.news_own == False) & (ev.alert_type != "news") & ~ev.repeat.astype(bool)  # noqa: E712
    return ev.assign(taken=taken)[["symbol", "session", "taken", "short_ts"]]


def flag(trades: pd.DataFrame, al: pd.DataFrame) -> pd.DataFrame:
    """``short_open``: the alert is one the short book takes and the long's entry is inside the short's hold."""
    t = trades.assign(session=trades.session.astype(str)).merge(al, on=["symbol", "session"], how="left")
    lt, st = pd.to_datetime(t.entry_ts, utc=True), pd.to_datetime(t.short_ts, utc=True)
    return t.assign(short_open=t.taken.fillna(False).astype(bool) & (lt >= st) & (lt < st + HOLD))


def trades(path: Path, split: str) -> pd.DataFrame:
    t = pd.read_csv(path, converters=CONV)
    return t[(t.cfg == CFG) & (t.status == "settled") & (t.split == split)].dropna(subset=["net", "entry_ts"])


def stats(t: pd.DataFrame) -> dict:
    fl, rest = t[t.short_open], t[~t.short_open]
    out = {"n_flagged": len(fl), "n_rest": len(rest), "all": float(t.net.mean()) if len(t) else None,
           "flagged": float(fl.net.mean()) if len(fl) else None, "rest": float(rest.net.mean()) if len(rest) else None}
    if len(fl) and len(rest):
        dist = np.asarray(boot_groups(rest.session.to_numpy(), rest.net.to_numpy(float), fl.session.to_numpy(),
                                      fl.net.to_numpy(float)), float)
        out.update(diff=out["rest"] - out["flagged"], p=float(((dist <= 0) | np.isnan(dist)).mean()))
    return out


def passed(st: dict) -> bool:
    return bool(st["n_flagged"] >= MIN_N and (st.get("flagged") or 0) < 0 and (st.get("diff") or 0) > 0)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    res = OUT / "result.json"
    if res.is_file():
        print(f"{res} exists: the run was made once and is not repeated")
        return 2
    if not argv:
        print(__doc__)
        return 1
    al = alerts()
    hold = stats(flag(trades(Path(argv[0]), "holdout"), al))
    out = {"prereg": "docs/findings/prereg_long_while_short_2026-11-20.md", "holdout": hold, "pass": passed(hold),
           "training_disclosed": stats(flag(trades(Path(argv[0]), "train"), al))}
    OUT.mkdir(parents=True, exist_ok=True)
    res.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
