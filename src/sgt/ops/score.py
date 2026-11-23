"""Money score (owner 2026-11-14): "the confidence score must actually work based on how much money we think we are
going to potentially make or lose".

What the tests support (prereg_money_score_2026-11-14, prereg_record_score_2026-11-14, both FAIL): nothing we have
ranks two trades of the same kind: not the size of the model's forecast, not the stock's own record. What IS known is
the average result of each KIND of trade. So the score of a trade is that average, in dollars on this position, with
the swing a normal outcome can have, and GO only when the average is positive. One place, so no book can quote a
different expectation from the one its monitor judges it by.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

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
    per: str = "trade"          # what ``ev`` is the average of: one trade, or one day held (a model call is one day)
    caveat: str | None = None   # what the tested figure cannot claim (audit 2026-11-15); shown under the score
    stopped: str | None = None  # a pre-registered stop rule has tripped: NO-GO whatever the tested average says
    verdict: bool = True        # False: this average cannot carry a GO / NO-GO (it is not of trades that can be placed)


def _alert(key: str, label: str, book: str, n: int) -> Kind:
    b = next(x for x in live_monitor.BOOKS if x.name == book)
    return Kind(key, label, b.expectancy, b.std, b.win_rate, n, b.source)


KINDS = {k.key: k for k in (
    # Walk-forward out-of-sample record of the 21 names, 2020-01-02 .. 2026-06-30 (the training window of both score
    # tests). One number per side: per-stock and per-forecast figures failed their tests.
    # Audit 2026-11-15: the figure is a backtest on a universe picked with hindsight and its held-out months were
    # never opened; by year the long-calls book lost in 2022 and in January to June 2026 (prereg_long_rules_2026-11-15).
    Kind("model_long", "daily model long call", 0.0025, None, 0.532, 3448,
         "3,448 long calls 2020 to mid-2026 (prereg_record_score_2026-11-14)", per="day",
         # 2026-11-17 (prereg_fresh_names): the same model on 80 large caps it never saw, ranked on 2019 volume, FAILED
         # its test: the book made money (+0.15% a call, 13,542 calls) but a call did not beat the average stock that
         # day (p 0.069). The tested average above stays; the label says what it could not show.
         caveat="Backtest on stocks chosen with hindsight, not yet proven live; its last half-year (Jan to Jun 2026) lost. "
                "Not confirmed on unseen stocks: on 80 it never saw a long call made +0.15% and did not beat the average "
                "stock that day."),
    Kind("model_short", "daily model short call", -0.0008, None, 0.497, 3405,
         "3,405 short calls 2020 to mid-2026 (prereg_record_score_2026-11-14)", per="day",
         caveat="Backtest on stocks chosen with hindsight, not yet proven live."),
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


FADE_CAVEAT = ("No GO / NO-GO: this average is of EVERY alert priced the way the backtest prices it, including shorts that "
               "could not have been placed (no shares to borrow, or the short-sale price test). It is not the result of "
               "the trades this book can make.")


def _placeable(db: OpsStore) -> str:
    """What the shorts with no exclusion flag made on real quotes (the nightly ledger), as a sentence; "" when the
    ledger cannot be read. Strategy review 2026-11-23: the card said GO on the strength of an average those trades
    do not share."""
    try:
        from sgt.fast import exec_book

        b = exec_book.summary(Path(db.path).resolve().parent.parent / "exec_model" / "ledger.jsonl")["short"]
        if b["modeled"]:
            return (f" On real quotes the {b['modeled']} past shorts with no exclusion flag made {b['model']:+.1%} a "
                    f"trade; {b['unresolved']} more could not be priced as placeable.")
    except Exception:  # noqa: BLE001 - the score must never fail a page or a text
        pass
    return ""


def _fade_now(db: OpsStore | None) -> Kind:
    """The fade's figure today: its tested average, replaced by the forward record while the drift guard is in alarm
    (the tested number is then known to be stale). An unreadable record never improves the expectation.

    Strategy review 2026-11-23: neither figure carries a verdict. Both are averages over every alert, placeable or
    not, and the forward record's +1.0% over 105 alerts was -1.0% without its five best trades, while the shorts that
    could be placed lost on real quotes. A card must not say GO on that. The verdict is advice on the page and never
    was a trading gate; removing it changes no trade."""
    k = replace(KINDS["fade"], verdict=False, caveat=FADE_CAVEAT)
    if db is None:
        return k
    k = replace(k, caveat=FADE_CAVEAT + _placeable(db))
    try:
        r = live_monitor.check_forward(db)
    except Exception:  # noqa: BLE001 - the score must never fail a page or a text; the tested figure stands
        return k
    if r.get("alarms") and r.get("n"):
        return Kind(k.key, k.label, float(r["mean"]), k.sd, float(r["win"]), int(r["n"]),
                    f"forward record of {int(r['n'])} alerts, every alert whether or not it could be shorted "
                    f"(drift guard in alarm; tested {KINDS['fade'].ev:+.1%})", caveat=k.caveat, verdict=False)
    return k


def _long_now(db: OpsStore | None) -> Kind:
    """The model long call today: its tested average, with the live long-calls ledger next to it once there is one, and
    NO-GO once that ledger is below its pre-registered stop (prereg_megacap_long_forward_2026-11-13: -5%). An
    unreadable ledger changes nothing: the tested figure stands and the book's own stop check decides."""
    k = KINDS["model_long"]
    if db is None:
        return k
    try:
        from sgt.megacap import longonly

        lo = longonly.ledger(Path(db.path).resolve().parent.parent)
    except Exception:  # noqa: BLE001 - the score must never fail a page or a text
        return k
    if not lo.get("sessions"):
        return k
    avg = f", average {lo['avg_call']:+.2%}" if lo.get("avg_call") is not None else ""
    live = f"Live since {lo['since'][5:]}: {lo['calls']} calls{avg}, ledger {lo['total']:+.1%}."
    if lo.get("stop"):
        return replace(k, caveat=live, stopped=f"the long-calls ledger is below its {longonly.STOP:.0%} stop rule")
    return replace(k, caveat=f"{k.caveat} {live}")


def for_trade(kind: str, dollars: float | None, sigma: float | None = None, db: OpsStore | None = None) -> dict:
    """Score of one trade: expected dollars (``usd``), the swing of a normal outcome (``swing``, one standard
    deviation), and ``go``. ``dollars`` = money in the position; ``sigma`` = the stock's daily swing for kinds
    without their own spread (model calls). Unknown kind or size -> fields None, go None (never a silent GO)."""
    k = _fade_now(db) if kind == "fade" else _long_now(db) if kind == "model_long" else KINDS.get(kind)
    if k is None:
        return {"kind": kind, "label": kind, "ev": None, "sd": None, "size": None, "usd": None, "swing": None, "win": None, "n": None,
                "go": None, "basis": "unknown kind of trade: no tested expectation", "per": "trade", "caveat": None,
                "stopped": None}
    size = abs(float(dollars)) if dollars is not None else None
    sd = k.sd if k.sd is not None else (float(sigma) if sigma is not None and sigma > 0 else None)
    return {"kind": k.key, "label": k.label, "ev": k.ev, "sd": sd, "size": size,
            "usd": None if k.ev is None or size is None else k.ev * size,
            "swing": None if sd is None or size is None else sd * size,
            "win": k.win, "n": k.n,
            "go": None if k.ev is None or not k.verdict else bool(k.ev > 0 and not k.stopped), "basis": k.basis,
            "per": k.per, "caveat": k.caveat, "stopped": k.stopped}


def resize(s: dict, dollars: float | None) -> dict:
    """The same score on another position size (one lookup of the kind, many trades of it)."""
    size = abs(float(dollars)) if dollars is not None else None
    return {**s, "size": size,
            "usd": None if s.get("ev") is None or size is None else s["ev"] * size,
            "swing": None if s.get("sd") is None or size is None else s["sd"] * size}


def line(s: dict) -> str | None:
    """One plain sentence for a text or a card: 'expected +$34 a day (swing about $990): GO'. A model call's figures
    are per day held (audit 2026-11-15: a card can span many sessions)."""
    if s.get("ev") is None:
        return None
    usd = s.get("usd")
    day = s.get("per") == "day"
    money = (f"{'+' if usd >= 0 else '-'}${abs(usd):,.0f}{' a day' if day else ''}" if usd is not None
             else f"{s['ev']:+.2%} per {'day' if day else 'trade'}")
    swing = f" (swing about ${s['swing']:,.0f})" if s.get("swing") is not None else ""
    stop = f" ({s['stopped']})" if s.get("stopped") else ""
    if s.get("go") is None:                 # an average that cannot carry a verdict (see FADE_CAVEAT)
        return f"average {money}{swing}: no GO / NO-GO (not an average of placeable trades)"
    return f"expected {money}{swing}: {'GO' if s['go'] else 'NO-GO'}{stop}"
