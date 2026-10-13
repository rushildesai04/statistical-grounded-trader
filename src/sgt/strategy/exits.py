"""Exit conditions (plan §7.2 ``exits``).

All exits are evaluated only while a position is open; with no position every exit
is ``False``. Price barriers use the *bar's own* high/low (the exit would have
fired intrabar), which keeps the runtime's bar-close decision consistent with the
broker-side protective stop the executor places. ``session_end`` consults the
session calendar for the window close so forced flattening is DST-correct.
"""
from __future__ import annotations

from typing import ClassVar, Literal

from sgt.core.types import Segment
from sgt.intraday.calendar import default_calendar
from sgt.strategy.base import Condition, EvalView, Family, Role

_EXIT_ONLY: frozenset[Role] = frozenset({Role.EXIT})


class StopLossPct(Condition):
    """Hard stop: bar low at or below ``entry * (1 - pct/100)``."""

    kind: Literal["stop_loss_pct"] = "stop_loss_pct"
    pct: float

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = _EXIT_ONLY

    def evaluate(self, view: EvalView) -> bool:
        pos = view.position
        if pos is None or self.pct <= 0:
            return False
        return view.bar.low <= pos.entry_price * (1.0 - self.pct / 100.0)


class TakeProfitPct(Condition):
    """Profit target: bar high at or above ``entry * (1 + pct/100)``."""

    kind: Literal["take_profit_pct"] = "take_profit_pct"
    pct: float

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = _EXIT_ONLY

    def evaluate(self, view: EvalView) -> bool:
        pos = view.position
        if pos is None or self.pct <= 0:
            return False
        return view.bar.high >= pos.entry_price * (1.0 + self.pct / 100.0)


class TrailingStopPct(Condition):
    """Trailing stop measured from the highest price seen since entry."""

    kind: Literal["trailing_stop_pct"] = "trailing_stop_pct"
    pct: float

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = _EXIT_ONLY

    def evaluate(self, view: EvalView) -> bool:
        pos = view.position
        if pos is None or self.pct <= 0:
            return False
        return view.bar.low <= pos.high_since_entry * (1.0 - self.pct / 100.0)


class TimeStopMinutes(Condition):
    """Time stop: flatten once the position has been held ``n`` minutes."""

    kind: Literal["time_stop_minutes"] = "time_stop_minutes"
    n: int

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = _EXIT_ONLY

    def evaluate(self, view: EvalView) -> bool:
        pos = view.position
        if pos is None or self.n <= 0:
            return False
        return pos.minutes_held(view.bar.ts) >= self.n


class SessionEnd(Condition):
    """Forced flatten ``minutes_before_close`` minutes before the window's close.

    ``window`` selects the regular-close (``rth``) or post-market close (``post``).
    """

    kind: Literal["session_end"] = "session_end"
    minutes_before_close: int = 5
    window: Literal["rth", "post"] = "rth"

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = _EXIT_ONLY

    def evaluate(self, view: EvalView) -> bool:
        if view.position is None:
            return False
        # Only fire in the segment that leads up to this window's close, so a stale
        # "rth" session_end does not flatten every post-market bar (all of which are
        # already past the RTH close).
        want = Segment.RTH if self.window == "rth" else Segment.POST
        if view.bar.segment is not want:
            return False
        try:
            bounds = default_calendar().bounds(view.bar.session_date)
        except ValueError:
            return False
        close = bounds.rth_close if self.window == "rth" else bounds.post_close
        remaining = (close - view.bar.ts).total_seconds() / 60.0
        return 0.0 <= remaining <= self.minutes_before_close
