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

## Rollout (each phase verified before the next)
0. Owner: Schwab developer approval, app (callback https://127.0.0.1), keys in keychain, `scripts/schwab_auth.py`.
1. Read-only connect: account hash, balances, positions (no orders). Confirms every [VERIFY] fact in
   `sgt.brokers.schwab`.
2. One approved $100 SPY limit buy, then sell: confirms order, fill, ledger, reconciliation end to end.
3. Enable: 60/40 -> fade shorts at $500 -> turn-of-month (2026-11-27). Fade size rises to $2,000 after 20 real
   fills; large-cap book / AAOI join when their forward gates pass.

## Expectations
Fade at $2,000 x ~9 trades/day x +1.1% (held-out) ~= $200/day before borrow fees, on the names Schwab can short.
60/40 and turn-of-month add a steady few hundred dollars a year on $6,000. The first weeks at $500 per fade trade
measure real fills and borrow coverage; size goes up as soon as real results track the paper record.
