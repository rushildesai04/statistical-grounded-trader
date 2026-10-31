# Pre-registration: MetaTradings QQQ 0DTE option calls (2026-10-30)

Committed BEFORE any option price is joined to these calls. Nothing below may change after scoring.

## Question
If we had bought every QQQ 0DTE option call in the MetaTradings trade-ideas channel and managed it by
the channel's own posted plan ("Trim @ 20% - 100%+", "SL : 50%"), would it have made money after costs?

## Data
- Calls: channel id -1001204455879, 2024-10-23 .. 2026-10-29, parsed by
  `sgt.signals_live.ideas.parse_idea` (444 option calls, all QQQ 0DTE). Edited messages excluded.
  `posted_at` = Telegram server time. Eligible: posted 09:30-15:00 ET on a trading day; first call per day.
- Contract: OCC symbol QQQ{YYMMDD}{C|P}{strike x 1000, 8 digits}, expiring the posting day.
- Prices: Alpaca historical option trade bars (1-minute, `/v1beta1/options/bars`). Bars exist only for
  minutes with trades; a minute without a bar carries no fill or exit event.

## Frozen rule (primary, R1 = the posted plan)
- Entry: buy limit = posted premium x 1.05, live from the first full minute after posting for 10 minutes.
  Filled in the first bar whose low <= limit, at min(limit, that bar's open). No fill -> no trade.
- Two equal legs: leg A take-profit at fill x 1.20, leg B take-profit at fill x 2.00; shared stop at
  fill x 0.50. From the bar AFTER the fill: stop if low <= stop (filled at min(stop, open)); take-profit
  if high >= TP (filled at TP). A bar touching both counts as the stop. Remaining legs exit at the open
  of the first bar at or after 15:50 ET (else the last bar before 15:50).
- Costs (no historical option quotes on our plan): $0.02 per share each side (half-spread) + $0.03 per
  contract fees each side, i.e. per trade: net = (exit - fill - 0.04 - 0.0006) / fill on premium paid.
- Unit = one call, equal premium per call.

## Statistics and verdict (computed once)
- Mean net return per call; bootstrap 95% lower bound (10,000 resamples, seed 20261030; one call per day,
  so calls are resampled iid).
- PASS only if ALL: (1) lower95 > 0; (2) mean net > 0 with costs doubled; (3) mean net > 0 in both
  chronological halves (split at the median call date).
- INVALID (no verdict) if > 10% of eligible calls have no option bars.

## Secondary (reported, never change the verdict)
R2 all out at +20% (stop -50%); R3 no take-profit, stop -50%, exit 15:50; R4 R1 but filled at the posted
premium (what the channel advertises vs what is achievable); win rate; median; P&L by call/put.

## What happens next
PASS -> the live bot may paper-trade these calls (options paper on Alpaca) as a pre-registered forward
test. FAIL -> option calls stay watch-only, like the stock calls (prereg_trade_ideas_2026-10-29: FAIL).
