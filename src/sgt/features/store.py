"""Point-in-time feature store — the leakage firewall.

A feature row keyed (ticker, as_of_ts) is built ONLY from data timestamped
strictly before as_of_ts. Labels are computed on the NEXT trading bar. There is
one sanctioned way to get model data — `get_training_frame` — and it cannot
return future information. `tests/test_leakage.py` enforces this.

Convention: as_of_ts is a trading day's close timestamp (from OHLCV). Social
features aggregate mentions with created_utc < as_of_ts. Price features use bars
up to and including that day. The label uses the following day's close.
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from sgt.config import get_settings
from sgt.db.models import Feature, Label, Mention, OHLCV
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert
from sgt.processing.sentiment import signed_score

FEATURE_SET_VERSION = "v1"


def build_features_and_labels(tau_bps: float | None = None) -> dict:
    """Materialize features + labels for every (ticker, trading-day). Returns
    counts. tau (dead-zone half-width) defaults to the round-trip cost so the
    model is never rewarded for calling direction inside the noise band."""
    tau = (tau_bps if tau_bps is not None else get_settings().cost_bps) / 10_000.0
    n_feat = n_lab = 0
    with get_session() as session:
        tickers = [t[0] for t in session.query(OHLCV.ticker).distinct().all()
                   if not t[0].startswith("^")]
        mentions = _load_mentions(session)
        for ticker in tickers:
            bars = _load_bars(session, ticker)
            if len(bars) < 7:
                continue
            df = pd.DataFrame(bars).sort_values("date").reset_index(drop=True)
            df["ret"] = df["adj_close"].pct_change()
            tmen = mentions.get(ticker, [])
            for i in range(5, len(df) - 1):  # need 5d history and a next bar
                as_of = df.loc[i, "date"]
                feat = _price_features(df, i)
                feat.update(_social_features(tmen, as_of))
                _upsert(session, Feature, {
                    "ticker": ticker, "as_of_ts": as_of,
                    "feature_set_version": FEATURE_SET_VERSION, **feat,
                }, ["ticker", "as_of_ts"])
                n_feat += 1

                fwd = df.loc[i + 1, "adj_close"] / df.loc[i, "adj_close"] - 1.0
                _upsert(session, Label, {
                    "ticker": ticker, "as_of_ts": as_of,
                    "fwd_return": float(fwd),
                    "cls": "up" if fwd > tau else "down" if fwd < -tau else "flat",
                    "vol_bucket": _vol_bucket(fwd),
                }, ["ticker", "as_of_ts"])
                n_lab += 1
    return {"features": n_feat, "labels": n_lab, "tau": tau}


def _load_bars(session, ticker: str) -> list[dict]:
    rows = session.query(OHLCV).filter(OHLCV.ticker == ticker).all()
    return [{"date": r.date, "adj_close": r.adj_close, "close": r.close,
             "volume": r.volume} for r in rows]


def _load_mentions(session) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for m in session.query(Mention).all():
        out.setdefault(m.ticker, []).append({
            "created_utc": m.created_utc, "author_hash": m.author_hash,
            "weight": m.weight, "signed": signed_score(m.sentiment_scores or {}),
        })
    for lst in out.values():
        lst.sort(key=lambda x: x["created_utc"])
    return out


def _price_features(df: pd.DataFrame, i: int) -> dict:
    close = df["adj_close"]
    ret_1d = float(close.iloc[i] / close.iloc[i - 1] - 1.0)
    ret_5d = float(close.iloc[i] / close.iloc[i - 5] - 1.0)
    vol_5d = float(df["ret"].iloc[i - 4:i + 1].std(ddof=0) or 0.0)
    return {"ret_1d": ret_1d, "ret_5d": ret_5d, "vol_5d": vol_5d}


def _social_features(mentions: list[dict], as_of) -> dict:
    """Aggregate ONLY mentions strictly before as_of_ts. This < is the firewall."""
    w24_lo = as_of - timedelta(hours=24)
    w6_lo = as_of - timedelta(hours=6)
    prev_lo = as_of - timedelta(hours=48)

    cur = [m for m in mentions if w24_lo <= m["created_utc"] < as_of]
    recent = [m for m in mentions if w6_lo <= m["created_utc"] < as_of]
    prev = [m for m in mentions if prev_lo <= m["created_utc"] < w24_lo]

    signed = [m["signed"] for m in cur]
    mean_s = sum(signed) / len(signed) if signed else 0.0
    net_s = sum(m["weight"] * m["signed"] for m in cur)
    disp = _std(signed)
    return {
        "mention_count_24h": float(len(cur)),
        "mention_count_6h": float(len(recent)),
        "unique_authors_24h": float(len({m["author_hash"] for m in cur if m["author_hash"]})),
        "weighted_mentions_24h": float(sum(m["weight"] for m in cur)),
        "mean_sentiment_24h": float(mean_s),
        "net_sentiment_24h": float(net_s),
        "sentiment_dispersion_24h": float(disp),
        "mention_delta_24h": float(len(cur) - len(prev)),
    }


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    return (sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5


def _vol_bucket(fwd_return: float) -> str:
    a = abs(fwd_return)
    return "low" if a < 0.01 else "high" if a > 0.04 else "mid"


FEATURE_COLUMNS = [
    "mention_count_24h", "mention_count_6h", "unique_authors_24h",
    "weighted_mentions_24h", "mean_sentiment_24h", "net_sentiment_24h",
    "sentiment_dispersion_24h", "mention_delta_24h", "ret_1d", "ret_5d", "vol_5d",
]


def get_training_frame(start=None, end=None) -> pd.DataFrame:
    """The ONLY sanctioned way models get data. Inner-joins features to labels
    on (ticker, as_of_ts); a label is always the NEXT bar, so the frame cannot
    leak future information."""
    with get_session() as session:
        fq = session.query(Feature)
        if start is not None:
            fq = fq.filter(Feature.as_of_ts >= start)
        if end is not None:
            fq = fq.filter(Feature.as_of_ts < end)
        feats = {(f.ticker, f.as_of_ts): f for f in fq.all()}
        labels = {(l.ticker, l.as_of_ts): l for l in session.query(Label).all()}

    rows = []
    for key, f in feats.items():
        lab = labels.get(key)
        if lab is None:
            continue
        row = {"ticker": key[0], "as_of_ts": key[1],
               "fwd_return": lab.fwd_return, "cls": lab.cls, "vol_bucket": lab.vol_bucket}
        for col in FEATURE_COLUMNS:
            row[col] = getattr(f, col)
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["as_of_ts", "ticker"]).reset_index(drop=True)
    return df
