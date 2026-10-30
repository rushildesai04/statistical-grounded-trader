"""Live runner: Telegram signals -> paper orders, with timers, guards and notifications.

One process owns the Telegram session (lock) and the signals paper account. Decisions
come from ``rules`` (pure); this module only does I/O, timing and bookkeeping. Every
signal, decision, order and fill is appended to ``reports/signals_live/YYYY-MM-DD.jsonl``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sgt.crowd.telegram_collector import SourceMessage, TelegramCollector
from sgt.crowd.telegram_parser import ParsedAlert, ParsedNews, parse_message
from sgt.signals_live.broker import BrokerError, PaperBroker
from sgt.signals_live.ideas import OptionIdea, StockIdea, parse_idea
from sgt.signals_live.notify import parse_command
from sgt.signals_live.rules import ET, AlertPlan, IdeaPlan, Limits, Skip, State, decide_alert, decide_idea

log = logging.getLogger(__name__)
JOURNAL = Path("reports/signals_live")
TICK_S = 1.0
ORDER_POLL_S = 3.0
SMS_POLL_S = 10.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Trade:
    kind: str                     # "alert" | "idea"
    symbol: str
    label: str                    # alert type or "idea"
    cids: list[str]
    status: str = "scheduled"     # scheduled -> entering -> open -> exiting -> closed | cancelled
    submit_at: datetime | None = None
    hold: timedelta | None = None
    cancel_at: datetime | None = None
    exit_at: datetime | None = None
    qty: int = 0
    entry_px: float | None = None
    exit_cost: float = 0.0
    exit_qty: int = 0
    plan: object = None
    meta: dict = field(default_factory=dict)


class Runner:
    def __init__(self, client, broker: PaperBroker, notify, sms=None, *, nvb: int, ideas: int,
                 limits: Limits = Limits(), collector: TelegramCollector | None = None):
        self.client, self.broker, self.notify, self.sms = client, broker, notify, sms
        self.nvb, self.ideas, self.lim = nvb, ideas, limits
        self.collector = collector
        self.state = State()
        self.trades: list[Trade] = []
        self.flattened_day = ""
        JOURNAL.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ journal / notify
    def journal(self, event: str, **kw) -> None:
        rec = {"ts": _now().isoformat(), "event": event, **kw}
        path = JOURNAL / f"{_now().astimezone(ET):%Y-%m-%d}.jsonl"
        with path.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    async def tell(self, text: str, important: bool = True) -> None:
        log.info("NOTIFY %s", text)
        await self.notify.send(text, important=important)

    # ------------------------------------------------------------------ startup
    async def reconcile(self) -> None:
        """Broker is the source of truth after a restart: track held symbols; positions
        with no protective orders (alert trades whose timers were lost) exit now."""
        positions = await self.broker.positions()
        for sym, p in positions.items():
            self.state.open_symbols.add(sym)
            t = Trade("recovered", sym, "restart", [f"recover-{sym}-{_now():%Y%m%d%H%M%S}"], status="open",
                      qty=int(float(p["qty"])), entry_px=float(p["avg_entry_price"]))
            self.trades.append(t)
        if positions:
            await self.tell(f"restart: holding {', '.join(sorted(positions))}; bracket orders stay, "
                            "others exit at the next check")
        self.journal("startup", positions=list(positions))

    # ------------------------------------------------------------------ messages
    async def on_message(self, chat_id: int, msg_id: int, posted: datetime, text: str, edited: bool) -> None:
        if self.collector is not None:
            try:
                self.collector.store(str(chat_id), SourceMessage(msg_id, posted, text))
            except Exception as exc:  # noqa: BLE001 - storage must never block trading
                log.error("store failed for %s/%s: %s", chat_id, msg_id, exc)
        if edited:
            return
        now = _now()
        if chat_id == self.nvb:
            for it in parse_message(text, posted, str(chat_id), msg_id):
                if isinstance(it, ParsedAlert) or (isinstance(it, ParsedNews) and it.has_news):
                    atype = it.alert_type if isinstance(it, ParsedAlert) else "news"
                    await self.handle_alert(it.symbol, atype, posted, now, msg_id)
        elif chat_id == self.ideas:
            idea = parse_idea(text)
            if isinstance(idea, StockIdea):
                await self.handle_idea(idea, posted, now, msg_id)
            elif isinstance(idea, OptionIdea):
                self.journal("option_idea_logged", msg_id=msg_id, idea=repr(idea))
                await self.tell(f"OPTION idea (not traded yet): {idea.underlying} {idea.strike:g}{idea.right} "
                                f"{idea.dte}DTE @ {idea.premium}", important=False)

    async def handle_alert(self, symbol: str, atype: str, posted: datetime, now: datetime, msg_id: int) -> None:
        price = await self._price(symbol)
        d = decide_alert(symbol, atype, posted, now, price, self.state, self.lim)
        self.journal("alert", symbol=symbol, type=atype, posted=posted, price=price, msg_id=msg_id,
                     decision=d.reason if isinstance(d, Skip) else "trade")
        if isinstance(d, Skip):
            await self.tell(f"skip {symbol} {atype}: {d.reason}", important=False)
            return
        self.state.done_today.add(("alert", atype, symbol))
        self.state.open_symbols.add(symbol)
        cid = f"nvb-{msg_id}-{symbol}"[:48]
        self.trades.append(Trade("alert", symbol, atype, [cid], submit_at=d.submit_at, hold=d.hold, qty=d.qty, plan=d))
        await self.tell(f"PLAN buy {d.qty} {symbol} ~${price:.2f} at {d.submit_at.astimezone(ET):%H:%M} ET "
                        f"({atype}), sell {self.lim.alert_hold_min} min later")

    async def handle_idea(self, idea: StockIdea, posted: datetime, now: datetime, msg_id: int) -> None:
        price = await self._price(idea.symbol)
        d = decide_idea(idea, posted, now, price, self.state, self.lim)
        self.journal("idea", idea=idea.__repr__(), posted=posted, price=price, msg_id=msg_id,
                     decision=d.reason if isinstance(d, Skip) else "trade")
        if isinstance(d, Skip):
            await self.tell(f"skip idea {idea.symbol}: {d.reason}")
            return
        self.state.done_today.add(("idea", "stock", idea.symbol))
        self.state.open_symbols.add(idea.symbol)
        cids = [f"idea-{msg_id}-{idea.symbol}-{i}"[:48] for i in range(len(d.legs))]
        t = Trade("idea", idea.symbol, "idea", cids, status="entering", cancel_at=now + d.cancel_after,
                  qty=sum(l.qty for l in d.legs), plan=d)
        self.trades.append(t)
        for cid, leg in zip(cids, d.legs):
            try:
                await self.broker.bracket_buy(idea.symbol, leg.qty, d.limit, leg.take_profit, leg.stop, cid)
            except BrokerError as exc:
                self.journal("order_error", cid=cid, error=str(exc))
                await self.tell(f"ORDER ERROR {idea.symbol}: {exc}")
        self.journal("idea_orders", symbol=idea.symbol, limit=d.limit, legs=[l.__dict__ for l in d.legs])
        await self.tell(f"BUY {t.qty} {idea.symbol} limit ${d.limit} | targets "
                        f"{', '.join(f'${l.take_profit}' for l in d.legs)} | stop ${d.legs[0].stop}")

    async def on_command(self, cmd: str) -> str:
        if cmd == "pause":
            self.state.paused, self.state.pause_reason = True, "owner pause"
            reply = "paused: no new entries (open trades keep their exits)"
        elif cmd == "resume":
            self.state.paused, self.state.pause_reason = False, ""
            reply = "resumed"
        elif cmd == "kill":
            self.state.paused, self.state.pause_reason = True, "owner kill"
            await self.broker.flatten_all()
            for t in self.trades:
                if t.status not in ("closed", "cancelled"):
                    t.status = "closed"
            self.state.open_symbols.clear()
            reply = "KILLED: all orders cancelled, all positions closed, paused. Send 'resume' to restart."
        elif cmd == "status":
            reply = await self.status_text()
        else:
            reply = "commands: status | pause | resume | kill"
        self.journal("command", cmd=cmd, reply=reply)
        return reply

    async def status_text(self) -> str:
        a = await self.broker.account()
        pnl = float(a["equity"]) - float(a["last_equity"])
        pos = await self.broker.positions()
        held = ", ".join(f"{s} {p.get('qty')}@{float(p.get('avg_entry_price') or 0):.2f} "
                         f"({float(p.get('unrealized_plpc') or 0):+.1%})" for s, p in sorted(pos.items())) or "none"
        mode = f"PAUSED ({self.state.pause_reason})" if self.state.paused else "ACTIVE"
        return f"{mode} | today P&L ${pnl:+,.0f} | equity ${float(a['equity']):,.0f} | open: {held}"

    # ------------------------------------------------------------------ timers
    async def _price(self, symbol: str) -> float | None:
        try:
            return await self.broker.last_price(symbol)
        except BrokerError as exc:
            log.warning("price %s: %s", symbol, exc)
            return None

    async def tick(self) -> None:
        now = _now()
        for t in self.trades:
            try:
                await self._advance(t, now)
            except BrokerError as exc:
                self.journal("broker_error", symbol=t.symbol, error=str(exc))
                log.error("trade %s: %s", t.symbol, exc)
        et = now.astimezone(ET)
        if et.time() >= self.lim.flatten_at and et.weekday() < 5 and self.flattened_day != et.date().isoformat():
            self.flattened_day = et.date().isoformat()
            active = [t for t in self.trades if t.status not in ("closed", "cancelled")]
            if not active and not await self.broker.positions():
                return  # nothing to close (e.g. started after hours): stay quiet
            await self.broker.flatten_all()
            for t in self.trades:
                if t.status not in ("closed", "cancelled"):
                    t.status = "closed"
            self.state.open_symbols.clear()
            await self.tell(f"end of day: flattened. {await self.status_text()}")

    async def _advance(self, t: Trade, now: datetime) -> None:
        if t.status == "scheduled" and t.submit_at and now >= t.submit_at:
            await self.broker.market(t.symbol, t.qty, "buy", t.cids[0])
            t.status = "entering"
            t.cancel_at = now + timedelta(seconds=60)
            self.journal("alert_entry_submitted", symbol=t.symbol, qty=t.qty, cid=t.cids[0])
        elif t.status == "entering" and t.kind == "alert":
            o = await self.broker.order(t.cids[0])
            if o and o["status"] == "filled":
                t.status, t.qty, t.entry_px = "open", int(float(o["filled_qty"])), float(o["filled_avg_price"])
                t.exit_at = datetime.fromisoformat(o["filled_at"].replace("Z", "+00:00")) + (t.hold or timedelta(0))
                self.journal("alert_filled", symbol=t.symbol, qty=t.qty, px=t.entry_px)
                await self.tell(f"BOUGHT {t.qty} {t.symbol} @ ${t.entry_px:.2f}; sell at "
                                f"{t.exit_at.astimezone(ET):%H:%M:%S} ET")
            elif t.cancel_at and now >= t.cancel_at and o and o["status"] not in ("filled", "partially_filled"):
                await self.broker.cancel(o["id"])
                t.status = "cancelled"
                self.state.open_symbols.discard(t.symbol)
                await self.tell(f"cancelled {t.symbol}: no fill in 60 s", important=False)
        elif t.status == "open" and t.kind in ("alert", "recovered") and (t.exit_at is None or now >= t.exit_at):
            if t.kind == "recovered" and await self._has_open_orders(t.symbol):
                t.kind = "idea"  # bracket-protected position from before the restart
                return
            cid = f"x-{t.cids[0]}"[:48]
            await self.broker.market(t.symbol, t.qty, "sell", cid)
            t.status, t.cids = "exiting", t.cids + [cid]
        elif t.status == "exiting":
            o = await self.broker.order(t.cids[-1])
            if o and o["status"] == "filled":
                px = float(o["filled_avg_price"])
                await self._closed(t, px * t.qty, t.qty)
        elif t.kind == "idea" and t.status in ("entering", "open"):
            await self._advance_idea(t, now)

    async def _advance_idea(self, t: Trade, now: datetime) -> None:
        filled_qty = exit_qty = 0
        entry_cost = exit_cost = 0.0
        pending_entries = []
        for cid in t.cids:
            o = await self.broker.order(cid)
            if o is None:
                continue
            fq = int(float(o.get("filled_qty") or 0))
            filled_qty += fq
            entry_cost += fq * float(o.get("filled_avg_price") or 0)
            if o["status"] in ("new", "accepted", "pending_new", "partially_filled"):
                pending_entries.append(o)
            for leg in o.get("legs") or []:
                if leg["status"] == "filled":
                    q = int(float(leg["filled_qty"]))
                    exit_qty += q
                    exit_cost += q * float(leg["filled_avg_price"])
        if t.status == "entering" and filled_qty and not pending_entries:
            t.status = "open"
        if filled_qty and t.entry_px is None:
            t.entry_px = entry_cost / filled_qty
            await self.tell(f"FILLED {filled_qty} {t.symbol} @ ${t.entry_px:.2f}")
        if pending_entries and t.cancel_at and now >= t.cancel_at:
            for o in pending_entries:
                await self.broker.cancel(o["id"])
            if not filled_qty:
                t.status = "cancelled"
                self.state.open_symbols.discard(t.symbol)
                await self.tell(f"cancelled {t.symbol} idea: no fill in {self.lim.idea_fill_timeout_min} min",
                                important=False)
                return
            t.status = "open"
        t.qty = filled_qty
        if filled_qty and exit_qty >= filled_qty:
            await self._closed(t, exit_cost, exit_qty)

    async def _has_open_orders(self, symbol: str) -> bool:
        pos = await self.broker.positions()
        if symbol not in pos:
            return False
        return float(pos[symbol].get("qty_available", pos[symbol]["qty"])) < float(pos[symbol]["qty"])

    async def _closed(self, t: Trade, exit_value: float, qty: int) -> None:
        t.status = "closed"
        self.state.open_symbols.discard(t.symbol)
        pnl = exit_value - (t.entry_px or 0) * qty
        pct = pnl / ((t.entry_px or 1) * qty) if qty else 0.0
        self.journal("closed", symbol=t.symbol, kind=t.kind, label=t.label, qty=qty, entry=t.entry_px,
                     exit=exit_value / qty if qty else None, pnl=pnl)
        await self.tell(f"SOLD {qty} {t.symbol} @ ${exit_value / qty:.2f}: {'+' if pnl >= 0 else '-'}"
                        f"${abs(pnl):,.0f} ({pct:+.1%}) [{t.label}]")

    async def check_daily_loss(self) -> None:
        if self.state.paused:
            return
        a = await self.broker.account()
        pnl = float(a["equity"]) - float(a["last_equity"])
        if pnl <= -self.lim.daily_loss_stop_usd:
            self.state.paused, self.state.pause_reason = True, f"daily loss ${pnl:,.0f}"
            self.journal("daily_loss_stop", pnl=pnl)
            await self.tell(f"DAILY LOSS STOP hit (${pnl:,.0f}): no new entries today. "
                            "Open trades keep their exits. Send 'resume' to override.")

    # ------------------------------------------------------------------ loops
    async def run_loops(self) -> None:
        last_orders = last_sms = 0.0
        loop = asyncio.get_running_loop()
        while True:
            t0 = loop.time()
            try:
                if t0 - last_orders >= ORDER_POLL_S:
                    last_orders = t0
                    await self.tick()
                    await self.check_daily_loss()
                else:
                    # alert entries must fire on the minute boundary: check schedules every tick
                    now = _now()
                    for t in self.trades:
                        if t.status == "scheduled" and t.submit_at and now >= t.submit_at:
                            await self._advance(t, now)
                if self.sms is not None and t0 - last_sms >= SMS_POLL_S:
                    last_sms = t0
                    for cmd in await self.sms.poll_commands():
                        await self.tell(await self.on_command(cmd))
            except Exception as exc:  # noqa: BLE001 - loop must survive; every error is logged + journaled
                log.exception("loop error")
                self.journal("loop_error", error=repr(exc))
            self.trades = [t for t in self.trades if t.status not in ("closed", "cancelled")]
            await asyncio.sleep(TICK_S)

    async def on_saved_message(self, text: str) -> None:
        if (cmd := parse_command(text)) is not None:
            await self.tell(await self.on_command(cmd))
