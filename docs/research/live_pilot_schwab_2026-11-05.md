# Live pilot at Charles Schwab (design, 2026-11-05)

## Owner decisions (2026-11-05)
- Real money at Schwab, $10,000 hard cap, at most $2,000 per order; every ENTRY needs "OK <code>" (fade: the F5
  entry-at-alert text with NO = exit). Exits are automatic and never need approval.
- Owner's rule (2026-11-05, supersedes "everything incl. AAOI" from earlier the same day): real money only on
  strategies that passed a test on data they never saw. Qualify now: alert fade (held-out +1.12%/trade, n 764),
  turn-of-month SPY (held-out PASS), 60/40. The large-cap book and AAOI run on paper and graduate to real money
  automatically when their forward records pass their pre-registered gates.

## Budget (sub-caps sum to the hard cap)
| strategy | sub-cap | sizing |
|---|---|---|
| alert fade (short) | $4,000 | $500 per trade at start (max 8 open); $2,000 per trade after the first 20 real fills track the paper record |
| 60/40 SPY/IEF | $4,000 | monthly rebalance |
| turn-of-month SPY | $2,000 | entry 2nd-to-last session (first: 2026-11-27), exit on the registered day |

## The borrow question, answered by trading
Schwab requires a located borrow for every short and shows availability per name. The fade path submits a
SELL_SHORT limit order on each approved signal; Schwab either fills it (shares located) or rejects it (no trade,
logged as "not borrowable at Schwab"). Requires a margin account. Within weeks this measures, with real fills,
which fade names Schwab can short and whether that subset earns what the paper record does; IBKR availability
stamps (from 2026-11-04) give the same answer for a second broker.
Known risk this measures: on history, the Alpaca easy-to-borrow subset of fade names lost (-0.55%/trade,
prereg_alert_fade_2026-11-01 secondary), so the names a broker can lend may fade less. The $500 start sizes
the cost of finding out; size rises only if Schwab's shortable subset tracks the paper record.

## Invariants (fail closed; each has a test before any live order)
1. **Own positions only.** The pilot tracks the shares IT bought per strategy (ledger). A sell is never larger than
   the pilot's own quantity of that symbol, so the owner's other Schwab holdings are never touched — including by
   FLATTEN.
2. **Caps checked against the broker, not memory.** Before every entry: pilot market value (own quantities x live
   quote) + open pilot orders + this order <= $10,000, order <= $2,000, strategy sub-cap respected. Any read
   failure = no order.
3. **Allowlist.** Long: SPY, IEF. Short: only symbols of live fade signals, opened by SELL_SHORT and closed by
   BUY_TO_COVER (stop +20% or 60 min / RTH close, exactly as the live book). Equities only; regular session only; limit orders only (marketable limit at most 0.2% through the quote), price sanity within 2% of the last
   quote.
4. **Kill switch.** PAUSE stops entries (existing). FLATTEN sells the pilot's own positions with limit orders and
   verifies by re-reading positions. A kill flag in the DB is checked immediately before every POST.
5. **Login lifecycle.** The Schwab refresh token lasts ~7 days. Day 6: SMS reminder. Expired or failing token:
   entries stop, owner is texted in plain words, exits still attempted; nothing retries a POST blindly (adapter
   already reconciles ambiguous submits).
6. **Idempotency.** One client order id per (strategy, decision, leg); the Schwab ledger refuses a second POST.
7. **Audit.** Every intent, approval, broker ack, fill, reject and cap decision is stored in the ops DB; the nightly
   digest shows real P&L per strategy next to its paper record.

## Red-team fixes (2026-11-05, .claude/work FINDINGS F-1..F-11; all built and tested before the phase that needs them)
- Login: the rotating refresh token lives in the ops DB on the Fly volume (read first), not keychain/env.
- Shorts: every fade short gets a broker-held BUY STOP at +20% immediately after the fill, so the position is
  protected even if the server or login fails; the 60-minute / close exit cancels it and covers with an escalating
  marketable limit, then a market order before the close as last resort. Positions are signed (short quantities
  read from Schwab); a cover never exceeds the broker short quantity.
- Day trades: the executor reads Schwab's day-trade status; while the old PDT limit applies to the account and
  equity is under $25k, an entry that would be the 4th day trade in 5 days is skipped (FINRA removed the PDT
  minimum effective 2026-06-04; brokers may phase in until 2027-10-20).
- Caps: one atomic reservation of notional in the ops DB before every POST (listener and cron share it).
- FLATTEN: pilot ledger quantities only; never a bulk close.
- Calendar: session bounds (incl. 13:00 early closes) from the exchange calendar.
- Client order ids are deterministic per (strategy, decision, leg), so a restart can never duplicate an order.

## Rollout (each phase verified before the next)
0. Owner: Schwab developer approval, app (callback https://127.0.0.1), keys in keychain, `scripts/schwab_auth.py`.
1. Read-only connect: account hash, balances, positions (no orders). Confirms every [VERIFY] fact in
   `sgt.brokers.schwab`.
2. One approved $100 SPY limit buy, then sell: confirms order, fill, ledger, reconciliation end to end.
3. Enable: 60/40 (needs login store, caps, FLATTEN, calendar) -> fade shorts at $500 (also needs broker
   stop, signed positions, day-trade guard) -> turn-of-month (2026-11-27). Fade size rises to $2,000 after 20 real
   fills; large-cap book / AAOI join when their forward gates pass.

## Expectations
Fade at $2,000 x ~9 trades/day x +1.1% (held-out) ~= $200/day before borrow fees, on the names Schwab can short.
60/40 and turn-of-month add a steady few hundred dollars a year on $6,000. The first weeks at $500 per fade trade
measure real fills and borrow coverage; size goes up as soon as real results track the paper record.

## Build status (2026-11-05)
| piece | where | red-team |
|---|---|---|
| rotating login on the Fly volume | `sgt.brokers.schwab_token_store` | F-1 |
| caps, pilot-only positions, deterministic ids | `sgt.pilot.ledger` | F-5 (part), F-6, F-8, F-11 |
| short sides, signed positions, cover cap, no short on a held long | `sgt.brokers.schwab` / `schwab_mapping` | F-4, F-8 |
| ledger-first orders, sync, reconcile (kill on mismatch), pilot-only FLATTEN | `sgt.pilot.executor` | F-5 |
| real fade shorts: entry, broker stop, escalating cover, PDT guard, real day stop | `sgt.pilot.fade_real` | F-2, F-3, F-9 |
| real 60/40 + turn-of-month (own OK per entry, close window, half-days) | `sgt.pilot.book_real`, cron `pilotbook` | F-7 |
| REAL / PAPER / FLATTEN / RESUME texts, login reminder | `scripts/sms_control.py`, `sgt.pilot.runtime` | - |

## Go-live runbook (owner, in order)
1. developer.schwab.com: app "Trader API - Individual", callback `https://127.0.0.1`; wait for "Ready For Use".
2. Mac: store the app key and secret in the keychain (each command prompts for the value; never paste them in chat):
   `security add-generic-password -s sgt -a schwab_app_key -w` and the same with `-a schwab_app_secret`.
   Then log in: `python scripts/schwab_auth.py --push-fly` (repeat every ~7 days; the server texts a reminder on day 6).
3. `fly secrets set SGT_SECRET_SCHWAB_ACCOUNT_LAST4=<last 4 digits> -a sgt-runner` (margin account).
4. Mac: `python scripts/schwab_check.py --last4 <digits>`: must show the day-trade fields present and the margin hint.
5. `fly secrets set SGT_PILOT_SCHWAB=1 -a sgt-runner`, then during market hours on the runner:
   `python /app/scripts/schwab_smoke.py --real-money` (1 IEF in and out). PASS = the path works end to end.
6. 60/40 and turn-of-month start by themselves: each entry is texted, reply OK. First TOM entry 2026-11-27.
7. Fade shorts: text REAL (code) once, then ARM each trading morning. PAPER stops real fades; FLATTEN closes all
   pilot positions; PAUSE stops every entry.
