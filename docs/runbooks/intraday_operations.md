# Intraday engine — operations runbook

Audience: the operator running research, shadow, paper and live trading on their own accounts. Design and rationale live in `docs/IMPLEMENTATION_PLAN.md`; this document is the how-to.

## 1. One-time setup

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev,crowd]"
.venv/bin/python scripts/init_db.py
```

Secrets are never put in `.env` or the repo. Store them in the OS keychain (service `sgt`), or as `SGT_SECRET_<NAME>` environment variables on headless hosts:

```bash
python -c "import keyring; keyring.set_password('sgt', 'alpaca_key_id', '...')"
python -c "import keyring; keyring.set_password('sgt', 'alpaca_secret_key', '...')"
python -c "import keyring; keyring.set_password('sgt', 'control_plane_token', '<long random string>')"
```

| Secret name | Used by |
|---|---|
| `alpaca_key_id`, `alpaca_secret_key` | Alpaca market data + paper trading |
| `alpaca_live_key_id`, `alpaca_live_secret_key` | Alpaca live trading (separate keys by design) |
| `control_plane_token` | Control-plane API bearer token (unset ⇒ every control call returns 503) |
| `telegram_api_id`, `telegram_api_hash` | Telegram read-only collector |

Broker API keys should be created with trading scope only — never transfer/withdrawal permissions.

### Account registry: broker names are explicit about real money

| `broker` value | Endpoint | Allowed modes |
|---|---|---|
| `sim` | in-process SimBroker | shadow |
| `alpaca_paper` | Alpaca paper | shadow (simulated), paper |
| `alpaca_live` | Alpaca live | live only |
| `alert_only` / `robinhood` | push alert; operator executes and records the fill | paper, live |

A plain `alpaca` value is rejected — rename existing rows to `alpaca_paper` or `alpaca_live`. The CLI and the runner both refuse any adapter whose liveness does not match the mode (a live-money adapter can never run under paper rules, and live mode can never silently run on a paper adapter).

## 2. Research loop (free data)

1. **Capture bars.** `scripts/capture_session.py --symbols ... --date YYYY-MM-DD --source yfinance` for recent days (yfinance keeps ~7–30 days of 1-minute data), or `--source alpaca` for consolidated SIP history older than 15 minutes on the free plan.
2. **Audit before trusting.** `scripts/data_audit.py ...` — read the zero-volume columns. yfinance reports zero volume for virtually all pre/post-market bars, which silently disables volume signals there (finding F-2). Do not draw conclusions about extended-hours behavior from yfinance data.
3. **Backtest.** `scripts/backtest_intraday.py --strategy strategies/trader_v0.yaml --symbols ... --start ... --end ...`. Read the exit-reason attribution and skip-reason tables, not just net PnL.
4. **Search.** `scripts/search.py --space strategies/search/trader_v0_space.yaml --start ... --end ... --lockbox-start ...`. Sessions from `--lockbox-start` onward are never touched by the walk-forward search. Selection prefers parameter plateaus, and every trial is counted for the deflated Sharpe ratio.
5. **Lockbox.** `search.py` opens the lockbox exactly once for the selected finalist and writes the result into the candidate card. A second opening for the same config is refused by design, so a strategy cannot be re-tuned against its own holdout.
6. **Telegram alerts.** `scripts/telegram_backfill.py --since ...` then `scripts/alert_event_study.py` to measure which alert types actually led to moves, and how late the alerts were.

7. **Weekly learning loop.** `scripts/learn_from_results.py --strategy strategies/trader_v0.yaml --start ... --end ...` writes `reports/learning_<date>.md`. It covers:
   - cost attribution (before vs after costs)
   - the outcome of every signal by context (time of day, price, float, relative volume, RSI, ...)
   - a learned filter scored only on later, unseen sessions
   - a two-half stability check, and rule-based recommendations

   Treat its recommendations as hypotheses. A change goes into a new strategy file (e.g. `strategies/trader_v1.yaml`), runs in shadow, and must pass the search + lockbox gates before it can go live. See `docs/findings/learning_2026-10-17.md` for the first pass.

A strategy that does not pass the acceptance criteria in plan §1 does not advance. That outcome is a finding, not a failure.

### Daily accumulation (run this every trading day)

Free yfinance 1-minute history only reaches back ~30 days (and ≤ ~8 days per request), so the only way to have research history later is to capture it daily before it expires. `scripts/capture_daily.py` does this end to end: it screens for the day's movers, tracks each one for `--keep-sessions` trading sessions after it was last seen (so quiet days are captured too, not just the day a symbol moved — otherwise the sample is selection-biased toward big moves), captures the last `--lookback-sessions` complete sessions for every active symbol into the immutable Parquet store (never overwriting, never re-fetching a session already on disk), and snapshots float. Float comes from two sources: a one-time SEC EDGAR backfill for symbols with no share snapshot yet, and a point-in-time **vendor float snapshot** (yfinance `floatShares` / `sharesOutstanding`) written as a `FloatOverrideRow` with `source="vendor:yfinance"`, effective at UTC-midnight of that session. The reference layer already prefers a `FloatOverrideRow` over the stale EDGAR dei proxy, so this is what lets a newly discovered microcap clear the universe gate on the same day (EDGAR dei share counts lag dilution badly on microcaps — e.g. WFF reported ~5M outstanding vs ~42M actual on 2026-10-12). Vendor snapshots are only ever written for the current session, never backdated, because a float read today does not tell you the float on a past day.

Run it after the post-market close (20:00 ET / 17:00 ET on early-close days) so the day's session is complete and gets captured in the same run. It is idempotent: a second run the same day writes nothing new. `--dry-run` discovers and prints the capture plan without writing anything; `--no-float` skips both float steps; `--watchlist` adds always-active symbols; `--report-out` writes the markdown summary to a file.

```bash
# After the post-market close, capture the day and refresh float:
.venv/bin/python scripts/capture_daily.py --watchlist WFF,YMAT,SPCX --report-out data/accumulation/$(date +%F).md
```

Schedule it with cron (22:30 ET ≈ 02:30 UTC covers the normal 20:00 ET post-close) or launchd on macOS. Cron example (document only — do not install blindly; set the venv path and working directory for your host):

```cron
# m h dom mon dow   — weekdays at 22:30 America/New_York
30 22 * * 1-5 cd /path/to/statistical-grounded-trader && TZ=America/New_York .venv/bin/python scripts/capture_daily.py --watchlist WFF,YMAT,SPCX --report-out "data/accumulation/$(date +\%F).md" >> data/accumulation/cron.log 2>&1
```

launchd example (`~/Library/LaunchAgents/ai.metalinked.sgt.capture-daily.plist`, `StartCalendarInterval` weekdays 22:30 local) runs the same command; prefer launchd on laptops that sleep, since it runs a missed job on wake while cron does not.

## 3. Promotion ladder

| Stage | How to run | Exit criteria |
|---|---|---|
| Shadow | `scripts/run_live.py --strategy ... --mode shadow --accounts sim --symbols WFF,YMAT --feed delayed_sip` | ≥ 4 weeks, signals and modeled fills match backtest expectations |
| Paper | `--mode paper --feed sip --risk <risk.yaml>` (requires a real-time consolidated feed) | ≥ 2 weeks, broker order lifecycle clean, realized slippage ≤ 1.5× modeled |
| Live micro → small → standard | `--mode live --feed sip --risk <risk.yaml> --confirm-live`, then arm via control plane | Plan §11.5 gates; promotion is a human decision, demotion is automatic |

Live mode refuses to start on a delayed or IEX-only feed, refuses a strategy without a validated decay baseline in the strategy registry, refuses `--allow-unknown-float`, and refuses an Alpaca live account that has shorting enabled (this system is long-only; disable shorting in the Alpaca account configuration first, so the broker itself rejects any accidental over-sell).

## 4. Control plane

```bash
python scripts/run_control_plane.py          # binds 127.0.0.1:8787 only
```

Every call needs `Authorization: Bearer <control_plane_token>`. Common operations:

| Action | Call |
|---|---|
| Status | `GET /control/status` |
| Arm paper | `POST /control/arm {"namespace":"paper","actor":"you","reason":"..."}` |
| Arm live | same, plus `"confirm":"ARM LIVE"` |
| Disarm (always allowed) | `POST /control/disarm {...}` |
| Promote a stage | `POST /control/strategies/{id}/stage {"namespace":"live","stage":"micro","actor":"you"}` |
| Disable an account | `POST /control/accounts/{id}/disable {...}` |
| Record a manual execution (alert-only accounts, e.g. Robinhood) | `POST /control/manual-fills {...}` |
| Audit trail | `GET /control/audit?namespace=live&limit=200` |

Every process start is **disarmed**. A restart mid-session therefore always requires a human to re-arm.

## 5. What the system does on its own

| Event | Automatic response |
|---|---|
| Daily loss limit hit (account or global) — measured on realized **plus unrealized** (mark-to-market every bar) | New entries blocked for the rest of the session and every open position in the breached scope is flattened with marketable exits; exits are never blocked |
| Daily profit lock reached | New entries blocked; exits still allowed |
| Learning budget / strategy budget exhausted (realized + unrealized, live only) | Positions in scope flattened; program disarmed / strategy disabled; audited |
| Position held where no broker-side stop can work (pre/post market, halts) | Protective exits are marketable limits (default 3% through the last close), re-priced every bar until filled |
| Decay monitors (CUSUM, rolling expectancy, drawdown budget, win-rate break, slippage drift) | Halve size → demote → disable → disarm, by severity; audited and alerted |
| Feed stale > 150 s in a tradable window | New entries blocked until fresh bars resume; with open positions a critical alert fires and missing broker stops are re-placed |
| Process restart | Starts disarmed; reconciles with the broker; re-seeds open positions with protective stops; rebuilds decay monitors from the trade journal and keeps any reduced size multiplier |
| Broker auth failure | Namespace disarmed; alert |
| Reconciliation mismatch (our ledger vs broker) | That account halted; alert |
| Halt on a held symbol | Exits wait for resumption; entries blocked |

## 6. Incident checklist

1. `POST /control/disarm` — exits remain allowed, protective stops stay at the broker.
2. `GET /control/audit` and the run's JSONL event log under `data/eventlog/` — every decision, order, fill and rejection is recorded with its inputs.
3. Compare broker positions with `GET /control/status`; resolve any mismatch at the broker before re-arming.
4. Re-arm only after the cause is understood. Promotion back up the ladder follows the normal gates.

Research tool. Not investment advice.
