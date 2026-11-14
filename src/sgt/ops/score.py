"""Money score (owner 2026-11-14): "the confidence score must actually work based on how much money we think we are
going to potentially make or lose".

What the tests support (prereg_money_score_2026-11-14, prereg_record_score_2026-11-14, both FAIL): nothing we have
ranks two trades of the same kind: not the size of the model's forecast, not the stock's own record. What IS known is
the average result of each KIND of trade. So the score of a trade is that average, in dollars on this position, with
the swing a normal outcome can have, and GO only when the average is positive. One place, so no book can quote a
different expectation from the one its monitor judges it by.
"""
from __future__ import annotations

from dataclasses import dataclass

from sgt.learning import live_monitor
from sgt.ops.store import OpsStore


@dataclass(frozen=True, slots=True)
class Kind:
    key: str
    label: str
    ev: float | None            # average net return per trade of this kind, after costs (None: not a call)
    sd: float | None            # spread of one trade's result (None: use the stock's own daily swing)
    win: float | None
    n: int | None
    basis: str


def _alert(key: str, label: str, book: str, n: int) -> Kind:
    b = next(x for x in live_monitor.BOOKS if x.name == book)
    return Kind(key, label, b.expectancy, b.std, b.win_rate, n, b.source)


KINDS = {k.key: k for k in (
    # Walk-forward out-of-sample record of the 21 names, 2020-01-02 .. 2026-06-30 (the training window of both score
    # tests). One number per side: per-stock and per-forecast figures failed their tests.
    Kind("model_long", "daily model long call", 0.0025, None, 0.532, 3448,
         "3,448 long calls 2020 to mid-2026 (prereg_record_score_2026-11-14)"),
    Kind("model_short", "daily model short call", -0.0008, None, 0.497, 3405,
         "3,405 short calls 2020 to mid-2026 (prereg_record_score_2026-11-14)"),
    _alert("alert_long", "alert long", "long book (paper)", 2242),
    _alert("fade", "alert fade (short)", "alert fade (no-news)", 1335),
    Kind("yardstick", "yardstick holding", None, None, None, None, "a basket held for comparison, not a call"),
)}
MODEL_STRATEGIES = ("megacap", "megacap_ext", "megacap_long")


def kind_of(strategy: str, side: str) -> str:
    """The kind of a daily-book trade. The overlay and the ETF books hold a basket: yardsticks, not calls."""
    if strategy in MODEL_STRATEGIES:
        return "model_long" if side == "long" else "model_short"
    return "yardstick"


def _fade_now(db: OpsStore | None) -> Kind:
    """The fade's expectation today: its tested average, replaced by the forward record while the drift guard is in
    alarm (the tested number is then known to be stale). An unreadable record never improves the expectation."""
    k = KINDS["fade"]
    if db is None:
        return k
    try:
        r = live_monitor.check_forward(db)
    except Exception:  # noqa: BLE001 - the score must never fail a page or a text; the tested figure stands
        return k
    if r.get("alarms") and r.get("n"):
        return Kind(k.key, k.label, float(r["mean"]), k.sd, float(r["win"]), int(r["n"]),
                    f"forward record of {int(r['n'])} alerts (drift guard in alarm; tested {k.ev:+.1%})")
    return k


def for_trade(kind: str, dollars: float | None, sigma: float | None = None, db: OpsStore | None = None) -> dict:
    """Score of one trade: expected dollars (``usd``), the swing of a normal outcome (``swing``, one standard
    deviation), and ``go``. ``dollars`` = money in the position; ``sigma`` = the stock's daily swing for kinds
    without their own spread (model calls). Unknown kind or size -> fields None, go None (never a silent GO)."""
    k = _fade_now(db) if kind == "fade" else KINDS.get(kind)
    if k is None:
        return {"kind": kind, "label": kind, "ev": None, "usd": None, "swing": None, "win": None, "n": None,
                "go": None, "basis": "unknown kind of trade: no tested expectation"}
    size = abs(float(dollars)) if dollars is not None else None
    sd = k.sd if k.sd is not None else (float(sigma) if sigma is not None and sigma > 0 else None)
    return {"kind": k.key, "label": k.label, "ev": k.ev,
            "usd": None if k.ev is None or size is None else k.ev * size,
            "swing": None if sd is None or size is None else sd * size,
            "win": k.win, "n": k.n, "go": None if k.ev is None else bool(k.ev > 0), "basis": k.basis}


def line(s: dict) -> str | None:
    """One plain sentence for a text or a card: 'expected +$34 on $13,400 (swing about $990): GO'."""
    if s.get("ev") is None:
        return None
    usd = s.get("usd")
    money = f"{'+' if usd >= 0 else '-'}${abs(usd):,.0f}" if usd is not None else f"{s['ev']:+.2%} per trade"
    swing = f" (swing about ${s['swing']:,.0f})" if s.get("swing") is not None else ""
    return f"expected {money}{swing}: {'GO' if s['go'] else 'NO-GO'}"
