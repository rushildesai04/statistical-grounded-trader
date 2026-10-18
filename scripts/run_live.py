"""Run a strategy in SHADOW / PAPER / LIVE mode behind the capital guardrails
(plan §3.1, §11, §13, §14).

The runtime ALWAYS starts disarmed. SHADOW places no real orders (SimBroker fills
against the feed). PAPER/LIVE place orders through broker adapters, but only once an
authenticated operator arms the master switch via the control plane — this script never
arms. LIVE additionally requires ``--confirm-live`` and a real-time consolidated feed.

Usage::

    python scripts/run_live.py --strategy strategies/trader_v0.yaml --mode shadow \
        --accounts sim1 --feed delayed_sip
    python scripts/run_live.py --strategy strategies/trader_v0.yaml --mode live \
        --accounts alpaca_live --feed sip --risk strategies/risk_live.yaml --confirm-live

Secrets (broker keys) are read only through sgt.core.secrets (keychain / SGT_SECRET_*);
account registry rows come from the DB. Feeds: delayed_sip (15-min), sip (real-time
consolidated), iex (real-time single-venue).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from sgt.accounts.registry import AccountSpec, all_accounts
from sgt.brokers.alpaca import AlpacaBroker
from sgt.brokers.alert_only import AlertOnlyBroker, LogNotifier, WebhookNotifier
from sgt.brokers.composite import AlertWithBrokerPositions
from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.brokers.ibkr import IBKRBroker
from sgt.brokers.schwab import SchwabBroker
from sgt.brokers.snaptrade import ReadOnlyPositionSource, SnapTradeBroker
from sgt.brokers.snaptrade_client import SnapTradeClient
from sgt.catalysts.reference import ReferenceDataProvider
from sgt.brokers.sim import SimBroker
from sgt.core.interfaces import BrokerAdapter
from sgt.core.secrets import get_secret
from sgt.core.types import Mode
from sgt.db.session import get_session, init_db
from sgt.intraday.sources import AlpacaPollingLiveSource
from sgt.risk.limits import RiskConfig
from sgt.engine.backtest import default_risk_config
from sgt.live.runner import AdapterModeMismatchError, FeedNotTradeableError, LiveRunner
from sgt.strategy.loader import load_strategy

_MODES = {"shadow": Mode.SHADOW, "paper": Mode.PAPER, "live": Mode.LIVE}
_FEEDS = {"delayed_sip", "sip", "iex"}


@dataclass(frozen=True)
class CliArgs:
    strategy: str
    mode: Mode
    account_ids: tuple[str, ...]
    feed: str
    risk_path: str | None
    confirm_live: bool
    flatten_on_exit: bool
    cash: float
    symbols: tuple[str, ...] = ()
    allow_unknown_float: bool = False
    allow_research_artifact: bool = False


def parse_args(argv: list[str] | None = None) -> CliArgs:
    ap = argparse.ArgumentParser(description="Run a strategy in shadow/paper/live mode.")
    ap.add_argument("--strategy", required=True, help="strategy YAML path")
    ap.add_argument("--mode", choices=sorted(_MODES), default="shadow")
    ap.add_argument("--accounts", required=True, help="comma-separated account ids")
    ap.add_argument("--feed", choices=sorted(_FEEDS), default="delayed_sip")
    ap.add_argument("--risk", default=None, help="risk config YAML (required for paper/live)")
    ap.add_argument("--confirm-live", action="store_true",
                    help="required acknowledgement to start a LIVE run")
    ap.add_argument("--flatten-on-exit", action="store_true",
                    help="flatten open positions on shutdown (default: keep broker stops)")
    ap.add_argument("--cash", type=float, default=100_000.0,
                    help="SHADOW starting cash for the SimBroker (default 100000)")
    ap.add_argument("--symbols", required=True,
                    help="comma-separated watchlist (symbols with open positions are always kept)")
    ap.add_argument("--allow-unknown-float", action="store_true",
                    help="shadow/paper only: admit symbols with no float data (universe float cap "
                         "unenforced for them). Refused in live mode.")
    ap.add_argument("--allow-research-artifact", action="store_true",
                    help="shadow/paper only: let a kind:model strategy load a quarantined / "
                         "promotion-ineligible model artifact. Refused in live mode.")
    ns = ap.parse_args(argv)
    accounts = tuple(a.strip() for a in ns.accounts.split(",") if a.strip())
    if not accounts:
        raise SystemExit("no accounts given")
    symbols = tuple(s.strip().upper() for s in ns.symbols.split(",") if s.strip())
    if not symbols:
        raise SystemExit("no symbols given")
    return CliArgs(ns.strategy, _MODES[ns.mode], accounts, ns.feed, ns.risk,
                   ns.confirm_live, ns.flatten_on_exit, ns.cash, symbols, ns.allow_unknown_float,
                   ns.allow_research_artifact)


def validate(args: CliArgs) -> None:
    """Fail-closed CLI gating applied before any broker/feed construction."""
    if args.mode is Mode.LIVE and not args.confirm_live:
        raise SystemExit("LIVE mode requires --confirm-live")
    if args.mode is Mode.LIVE and args.feed != "sip":
        raise SystemExit("LIVE mode requires --feed sip (real-time consolidated)")
    if args.mode is Mode.LIVE and args.allow_unknown_float:
        raise SystemExit("--allow-unknown-float is refused in LIVE mode")
    if args.mode is Mode.LIVE and args.allow_research_artifact:
        raise SystemExit("--allow-research-artifact is refused in LIVE mode")
    if args.mode in (Mode.PAPER, Mode.LIVE) and not args.risk_path:
        raise SystemExit(f"{args.mode.value} mode requires --risk <config.yaml>")


def _risk_config(args: CliArgs) -> RiskConfig:
    if args.risk_path:
        return RiskConfig.from_yaml(args.risk_path)
    # SHADOW only: a permissive config is acceptable because no real capital is at risk.
    return default_risk_config(load_strategy(args.strategy))


def _build_adapter(spec: AccountSpec, mode: Mode, cash: float) -> BrokerAdapter:
    """Build an account's adapter from its EXPLICIT broker string and refuse any adapter
    whose liveness does not match the run mode (L-C).

    Broker strings are explicit: ``sim``/``shadow`` (SimBroker), ``alpaca_paper`` /
    ``alpaca_live``, ``ibkr_paper`` / ``ibkr_live``, ``schwab_live`` (no sandbox exists),
    ``snaptrade_live`` (trade-capable SnapTrade link), ``robinhood_snaptrade`` (alerts +
    read-only Robinhood positions), ``alert_only``/``robinhood`` (alert channel only).
    Broker-side account ids come from the keychain (``<broker>_account_ref_<account_id>``).
    Plain ``alpaca`` is rejected because its endpoint
    would silently depend on the mode. Liveness (``is_paper``) is resolved from the broker
    string BEFORE construction so a mismatch fails fast without needing broker secrets:
    LIVE requires a live adapter; PAPER/SHADOW refuse a live-capable one."""
    broker = spec.broker.lower()
    if broker == "alpaca":
        raise SystemExit(
            f"account {spec.account_id!r}: broker 'alpaca' is ambiguous (its endpoint would "
            "depend on the run mode). Rename it to 'alpaca_paper' or 'alpaca_live' in the "
            "account registry.")
    paper_by_broker = {"sim": True, "shadow": True, "alpaca_paper": True, "ibkr_paper": True,
                       "alpaca_live": False, "alert_only": False, "robinhood": False,
                       "robinhood_snaptrade": False, "schwab_live": False,
                       "snaptrade_live": False, "ibkr_live": False}
    if broker not in paper_by_broker:
        raise SystemExit(f"account {spec.account_id!r} has unsupported broker {spec.broker!r}")
    is_paper = paper_by_broker[broker]
    if mode is Mode.LIVE and is_paper:
        raise SystemExit(f"account {spec.account_id!r}: LIVE mode requires a live adapter, "
                         f"got paper broker {spec.broker!r}")
    if mode in (Mode.PAPER, Mode.SHADOW) and not is_paper:
        raise SystemExit(f"account {spec.account_id!r}: {mode.value} mode refuses a live-capable "
                         f"adapter (broker {spec.broker!r}); use a paper/sim account")

    if broker in ("sim", "shadow"):
        return SimBroker(spec.account_id, DEFAULT_COST_MODEL, cash)
    if broker == "alpaca_paper":
        return AlpacaBroker(spec.account_id, paper=True)
    if broker == "alpaca_live":
        return AlpacaBroker(spec.account_id, paper=False)
    if broker == "schwab_live":  # official Trader API; no sandbox exists
        return SchwabBroker(spec.account_id, _account_ref("schwab", spec.account_id))
    if broker == "snaptrade_live":  # trade-capable SnapTrade connection (Webull, E*TRADE, ...)
        return SnapTradeBroker(spec.account_id, _account_ref("snaptrade", spec.account_id),
                               trade_enabled=True)
    if broker in ("ibkr_paper", "ibkr_live"):
        return IBKRBroker(spec.account_id, _account_ref("ibkr", spec.account_id),
                          paper=broker == "ibkr_paper")
    notifier = _notifier()
    if broker == "robinhood_snaptrade":
        # No official Robinhood equities API: orders go to the operator as alerts, while
        # positions/cash come from Robinhood via a read-only SnapTrade connection.
        source = ReadOnlyPositionSource(spec.account_id, _account_ref("snaptrade", spec.account_id),
                                        client=_snaptrade_client())
        return AlertWithBrokerPositions(spec.account_id, notifier, source)
    # alert_only / robinhood: a live, operator-facing alert channel (places no orders itself).
    return AlertOnlyBroker(spec.account_id, notifier)


def _recent_alert_symbols() -> set[str]:
    """Tickers alerted by TradingView in the current session (since 04:00 ET today)."""
    from datetime import datetime, time as dtime, timezone
    from zoneinfo import ZoneInfo

    from sgt.crowd.tradingview import recent_tradingview_symbols

    et = ZoneInfo("America/New_York")
    since = datetime.combine(datetime.now(et).date(), dtime(4, 0), tzinfo=et).astimezone(timezone.utc)
    with get_session() as session:
        return set(recent_tradingview_symbols(session, since))


def _account_ref(broker: str, account_id: str) -> str:
    """Broker-side account identifier (Schwab last 4 digits, SnapTrade account id, IBKR
    account code), kept in the keychain as ``<broker>_account_ref_<account_id>`` so account
    numbers never live in the database or repo."""
    return get_secret(f"{broker}_account_ref_{account_id}")


def _notifier():
    try:
        return WebhookNotifier()
    except ValueError:
        return LogNotifier()


def _snaptrade_client() -> SnapTradeClient:
    return SnapTradeClient(
        client_id=get_secret("snaptrade_client_id"),
        consumer_key=get_secret("snaptrade_consumer_key"),
        user_id=get_secret("snaptrade_user_id"),
        user_secret=get_secret("snaptrade_user_secret"),
    )


def build_runner(args: CliArgs) -> LiveRunner:
    init_db()
    with get_session() as session:
        registry = {a.account_id: a for a in all_accounts(session)}
    specs: list[AccountSpec] = []
    for aid in args.account_ids:
        spec = registry.get(aid)
        if spec is None:
            if args.mode is Mode.SHADOW:
                spec = AccountSpec(aid, "sim", "cash", True, 1.0, 0.0)
            else:
                raise SystemExit(f"unknown account {aid!r}; register it first")
        specs.append(spec)
    adapters = {s.account_id: _build_adapter(s, args.mode, args.cash) for s in specs}
    source = AlpacaPollingLiveSource(feed=args.feed)
    return LiveRunner(
        args.strategy, args.mode, specs, source=source, adapters=adapters,
        risk_config=_risk_config(args), flatten_on_exit=args.flatten_on_exit,
        reference=ReferenceDataProvider(), initial_watchlist=args.symbols,
        alert_symbols=_recent_alert_symbols,
        allow_unknown_float=args.allow_unknown_float,
        allow_research_artifact=args.allow_research_artifact)


def _print_arm_instructions(runner: LiveRunner, mode: Mode) -> None:
    print(f"[run_live] started run {runner.run_id} in {mode.value} mode — DISARMED.")
    if mode in (Mode.PAPER, Mode.LIVE):
        confirm = "ARM LIVE" if mode is Mode.LIVE else "arm"
        print("[run_live] no orders will be placed until an operator arms the switch:")
        print(f'           POST /control/arm  {{"namespace":"{mode.value}",'
              f'"actor":"<you>","reason":"<why>","confirm":"{confirm}"}}')
        if mode is Mode.LIVE:
            print("[run_live] LIVE also requires promoting the strategy off the shadow rung "
                  "via POST /control/strategies/<id>/stage.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    validate(args)
    load_strategy(args.strategy)  # validate the grammar before touching brokers
    runner = build_runner(args)

    async def _go() -> None:
        await runner.startup()
        _print_arm_instructions(runner, args.mode)
        await runner.serve()

    try:
        asyncio.run(_go())
    except (FeedNotTradeableError, AdapterModeMismatchError) as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
