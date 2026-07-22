"""Sentiment scoring with a swappable backend.

`finbert` uses ProsusAI/finbert (finance-domain, 3-class) when torch/transformers
are installed. `lexicon` is a deterministic keyword fallback so the pipeline —
and the test suite — runs with no ML deps or GPU. `auto` picks finbert if
importable, else lexicon.

Every scorer reports a `model_version` string stamped onto each mention for
reproducibility.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from sgt.config import get_settings

# Signed sentiment scores are collapsed to a label + {pos,neg,neu} dict.
LABELS = ("positive", "negative", "neutral")


class SentimentScorer(ABC):
    model_version: str = "unknown"

    @abstractmethod
    def score(self, texts: list[str]) -> list[dict]:
        """Return list of {label, scores:{positive,negative,neutral}}."""
        raise NotImplementedError


class LexiconScorer(SentimentScorer):
    """Deterministic fallback. Not accurate — a floor that keeps the pipeline
    runnable and tests hermetic. Do not ship as the production scorer."""

    model_version = "lexicon-v1"
    _POS = {"moon", "buy", "bull", "bullish", "calls", "long", "up", "green",
            "rocket", "pump", "gain", "gains", "win", "beat", "strong", "yolo"}
    _NEG = {"puts", "bear", "bearish", "short", "down", "red", "crash", "dump",
            "loss", "losses", "sell", "weak", "miss", "bankrupt", "rug", "drill"}

    def score(self, texts: list[str]) -> list[dict]:
        out = []
        for t in texts:
            toks = (t or "").lower().split()
            p = sum(w in self._POS for w in toks)
            n = sum(w in self._NEG for w in toks)
            total = p + n
            if total == 0:
                out.append({"label": "neutral",
                            "scores": {"positive": 0.0, "negative": 0.0, "neutral": 1.0}})
                continue
            pos, neg = p / total, n / total
            label = "positive" if pos > neg else "negative" if neg > pos else "neutral"
            out.append({"label": label,
                        "scores": {"positive": pos, "negative": neg,
                                   "neutral": max(0.0, 1.0 - pos - neg)}})
        return out


class FinBertScorer(SentimentScorer):
    def __init__(self, model_name: str | None = None, batch_size: int = 32):
        from transformers import (  # lazy: only when this backend is chosen
            AutoModelForSequenceClassification, AutoTokenizer,
        )
        import torch

        self._torch = torch
        self.model_name = model_name or get_settings().sentiment_model
        self.model_version = self.model_name
        self.batch_size = batch_size
        self._tok = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
        self._model.eval()
        # id2label from FinBERT config: {0:positive,1:negative,2:neutral} (varies)
        self._id2label = {int(k): v.lower() for k, v in self._model.config.id2label.items()}

    def score(self, texts: list[str]) -> list[dict]:
        torch = self._torch
        out: list[dict] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t or "" for t in texts[i:i + self.batch_size]]
            enc = self._tok(batch, return_tensors="pt", padding=True,
                            truncation=True, max_length=256)
            with torch.no_grad():
                probs = torch.softmax(self._model(**enc).logits, dim=-1)
            for row in probs:
                scores = {"positive": 0.0, "negative": 0.0, "neutral": 0.0}
                for idx, p in enumerate(row.tolist()):
                    scores[self._id2label.get(idx, "neutral")] = float(p)
                label = max(scores, key=scores.get)
                out.append({"label": label, "scores": scores})
        return out


def get_scorer(backend: str | None = None) -> SentimentScorer:
    backend = backend or get_settings().sentiment_backend
    if backend == "lexicon":
        return LexiconScorer()
    if backend == "finbert":
        return FinBertScorer()
    # auto
    try:
        return FinBertScorer()
    except Exception:
        return LexiconScorer()


def signed_score(scores: dict) -> float:
    """Collapse to a scalar in [-1, 1]: positive prob minus negative prob."""
    return float(scores.get("positive", 0.0) - scores.get("negative", 0.0))
