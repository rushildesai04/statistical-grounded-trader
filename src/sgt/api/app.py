"""Read-only signal API. Serves the latest feature-derived signals with the top
contributing features — explainability is a product requirement, not a nicety.

The base serves the logistic baseline's prediction until a model is promoted in
the registry. Every response carries a not-investment-advice disclaimer.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, Query

from sgt.features.store import FEATURE_COLUMNS, get_training_frame
from sgt.models.baselines import LogisticBaseline

app = FastAPI(title="statistical-grounded-trader", version="0.1.0")

DISCLAIMER = "Research tool. Not investment advice."


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/signals")
def signals(date: str | None = Query(default=None, description="YYYY-MM-DD (UTC)")) -> dict:
    """Signals for the most recent as_of_ts on or before `date`."""
    df = get_training_frame()
    if df.empty:
        return {"date": date, "signals": [], "disclaimer": DISCLAIMER}

    cutoff = _parse_date(date)
    train = df[df["as_of_ts"] < cutoff] if cutoff else df.iloc[:-1]
    if train.empty:
        return {"date": date, "signals": [], "disclaimer": DISCLAIMER}

    latest_ts = df[df["as_of_ts"] <= cutoff]["as_of_ts"].max() if cutoff else df["as_of_ts"].max()
    live = df[df["as_of_ts"] == latest_ts]

    model = LogisticBaseline().fit(train)
    preds = model.predict(live)

    out = []
    for (_, row), pred in zip(live.iterrows(), preds):
        contribs = sorted(
            ((c, float(row[c])) for c in FEATURE_COLUMNS),
            key=lambda kv: abs(kv[1]), reverse=True,
        )[:5]
        out.append({
            "ticker": row["ticker"],
            "signal": pred,
            "top_features": {k: v for k, v in contribs},
        })
    return {"as_of": str(latest_ts), "model": "logistic-baseline",
            "signals": out, "disclaimer": DISCLAIMER}


def _parse_date(date: str | None):
    if not date:
        return None
    return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
