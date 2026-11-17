"""Skip the short on a stock the channel already alerted before the open that day
(docs/findings/prereg_fade_premarket_warm_2026-11-17.md).

    .venv/bin/python scripts/fade_premarket_warm_study.py      # run ONCE (frozen prereg)

Report only: nothing here changes a book."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fade_repeat_days_study import _p, load, mark_repeats  # noqa: E402
from trader_rules_long_study import boot_groups  # noqa: E402  (seed 20261112, 10,000 resamples)

OUT = Path("reports/fade_premarket_warm")
DB, CHANNEL = "sgt.db", "-1"
BETA = Path("reports/fade_beta_unknown/events.csv")
ET = "America/New_York"
OPEN_MIN = 9 * 60 + 30
ALPHA, MIN_TRAIN, MIN_HOLD, BETA_MAX = 0.05, 100, 30, 3.0


def premarket_days(db: str = DB) -> set[tuple[str, str]]:
    """(symbol, Eastern calendar day) pairs with at least one unedited channel alert of any type before 09:30 ET."""
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        a = pd.read_sql("SELECT a.symbol, a.posted_at FROM crowd_alerts a JOIN telegram_messages m "
                        "USING (channel, message_id) WHERE m.edited_at IS NULL", c)
    return warm_pairs(a)


def warm_pairs(a: pd.DataFrame) -> set[tuple[str, str]]:
    et = pd.to_datetime(a.posted_at, utc=True, format="ISO8601").dt.tz_convert(ET)
    early = (et.dt.hour * 60 + et.dt.minute) < OPEN_MIN
    return set(zip(a.symbol[early], et.dt.strftime("%Y-%m-%d")[early]))


def rule_set(d: pd.DataFrame, beta: pd.DataFrame) -> pd.DataFrame:
    """The live rule set: no company news, not a 7-day repeat, beta below 3 or unknown."""
    d = mark_repeats(d)
    b = beta[["symbol", "session", "beta"]].assign(session=beta.session.astype(str)).drop_duplicates(["symbol", "session"])
    d = d.merge(b, on=["symbol", "session"], how="left")
    return d[(d.news_own == 0) & ~d.repeat & ~(d.beta >= BETA_MAX)]


def stats(b: pd.DataFrame) -> dict:
    b = b.dropna(subset=["net"])
    kept, sk = b[~b.warm], b[b.warm]
    out = {"n": int(len(b)), "n_kept": int(len(kept)), "n_skipped": int(len(sk)), "warm_share": float(b.warm.mean()) if len(b) else None,
           "all": float(b.net.mean()) if len(b) else None, "kept": float(kept.net.mean()) if len(kept) else None,
           "skipped": float(sk.net.mean()) if len(sk) else None, "skipped_total": float(sk.net.sum()),
           "kept_win": float((kept.net > 0).mean()) if len(kept) else None,
           "skipped_win": float((sk.net > 0).mean()) if len(sk) else None}
    if len(kept) and len(sk):
        dist = boot_groups(kept.session.to_numpy(), kept.net.to_numpy(float), sk.session.to_numpy(), sk.net.to_numpy(float))
        out.update(diff=out["kept"] - out["skipped"], p=_p(dist), p_opposite=_p(-dist))
    return out


def train_pass(st: dict) -> bool:
    return bool(st["n_skipped"] >= MIN_TRAIN and (st.get("diff") or 0) > 0 and st.get("p", 1.0) < ALPHA
                and st["skipped_total"] <= 0)


def by_time(b: pd.DataFrame) -> dict:
    et = pd.to_datetime(b.posted_at, utc=True).dt.tz_convert(ET)
    m = et.dt.hour * 60 + et.dt.minute
    return {name: stats(b[(m >= lo) & (m < hi)]) for name, lo, hi in (("09:30-10:00", 570, 600), ("10:00-11:00", 600, 660),
                                                                    ("11:00 on", 660, 960))}


def main() -> int:
    if (OUT / "result.json").is_file():
        print(f"{OUT / 'result.json'} exists: this study was run once and is not repeated")
        return 2
    warm = premarket_days()
    d = load()
    d["warm"] = [(s, x) in warm for s, x in zip(d.symbol, d.session)]
    rs = rule_set(d, pd.read_csv(BETA))
    rs = rs.assign(warm=[(s, x) in warm for s, x in zip(rs.symbol, rs.session)])
    dev, hold = rs[rs.split == "dev"], rs[rs.split == "holdout"]
    train = stats(dev)
    out = {"prereg": "docs/findings/prereg_fade_premarket_warm_2026-11-17.md", "train_rule_set": train,
           "train_pass": train_pass(train), "holdout_rule_set": None, "adopt_candidate": False,
           "secondary": {"train_all": stats(d[d.split == "dev"]), "train_news": stats(d[(d.split == "dev") & (d.news_own == 1)]),
                         "train_by_time": by_time(dev)}}
    if out["train_pass"]:
        h = stats(hold)
        out["holdout_rule_set"] = h
        out["adopt_candidate"] = bool(h["n_skipped"] >= MIN_HOLD and (h.get("diff") or 0) > 0)
    OUT.mkdir(parents=True, exist_ok=True)
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
