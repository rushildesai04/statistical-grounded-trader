# Execution model v1: what a real order would have got (frozen 2026-11-21 before any result under it)

Owner, 2026-11-21 (phase 2 of the system audit): "Replace idealized fill assumptions with a documented execution model
using timestamped trades and quotes where available. Account for spread, order type, liquidity, latency, halts, and
short availability. Keep candle-low results explicitly labeled as stress scenarios ... Preserve the original ledger."

Status: FROZEN in its own commit before the model was run on any trade. What was known when it was written: the
audit's read of the original record (`system_audit_2026-11-21.md`), and ONE probe of the data (ZJK 2026-11-20, 5 quotes
and 5 trades at 14:54:10 UTC: bid $1.41, ask $1.42) to learn that the official quotes can be read. No fill under this
model had been computed. Changes after this commit are amendments, dated, below the line at the end.

## 1. What it replaces
| | Original record ("official") | This model |
|---|---|---|
| Decision time | not used: the short is priced at the minute after alert + 10 s | the bot's own decision (`est_entry_at`; a long: when its row was booked) |
| Entry price | the open (first trade) of a 1-minute bar | the standing best bid (a short sale) or best ask (a buy) when the order arrives |
| Spread | an estimate by price bucket, charged as a cost (0.7 to 1.3% round trip) | paid in the fills themselves: sell at the bid, buy at the ask |
| Stop fill | the stop price exactly (the bar's open on a gap) | the quote after the first trade through the stop |
| Order of stop and target in one minute | stop first, by assumption | the order of the trades themselves |
| Halt | not modelled | a fill waits for the first trade after the halt |
| Borrow | a list, not in the result | a flag on the trade: listed, not listed, brokers disagree |

The original ledger is not changed by anything here. Both are shown side by side for every trade.

## 2. Data
Official (consolidated) trades and best-bid/best-ask quotes with exchange timestamps, free once 15 minutes old, and
the stored official 1-minute bars. A quote lookup is "the last quote at or before time T". Trades are cleaned as the
research cleans them (`sgt.decision.intrabar.clean_trades`: price-setting conditions only).

## 3. Orders and fills
Order latency `L` = 1.0 s (decision to the order being live at the exchange). It cannot be measured without a broker;
5 s is a stress scenario.

**Short (alert fade).**
- Entry: a market sell-short sent at the decision. Fill = best bid at decision + L.
- Stop: a buy stop resting from the entry, level = entry fill x 1.20. It triggers at the first official trade at or
  above the level within 60 minutes of the entry. Fill = max(best ask at trigger + L, the level).
- Time exit: a market buy 60 minutes after the entry fill, or 60 s before the session's close if that is earlier.
  Fill = best ask at that time + L.
- Costs: none added for spread (it is in the fills). Locate 0.5%, as in the tested rule. Commission zero, as in the
  tested rule (a per-share commission would add to both ledgers alike).

**Long (alert long book).**
- Entry: a market buy sent when the bot booked the trade. Fill = best ask at that time + L. If that fill is at or
  below the setup's stop, no trade (the order would not be sent): shown as "no trade under the model".
- Stop: a sell stop at the setup's stored stop. Triggers at the first trade at or below it. Fill = min(best bid at
  trigger + L, the stop).
- Target: a resting limit sell at entry fill + 2 x (entry fill - stop). Filled at the target at the first trade at or
  above it. Whichever of stop and target trades first decides.
- Red-candle exit (trades under rules "A2" only): if the entry's minute ends with its last trade below the entry fill
  and neither stop nor target traded, a market sell at the next minute's start. Fill = best bid at that time + L.
- Time exit: a market sell 30 minutes after the entry fill (or 60 s before the close). Fill = best bid + L.
- Costs: none added (the spread is in the fills).

**A fill that cannot be read from a quote.** When there is no usable quote at T (none on the tape, bid or ask zero,
ask below bid), or the stock looks halted at T (no official trade in the 60 s after T), the fill is the first official
trade after T and the trade is flagged; with no trade before the session ends, the exit is the session's last trade
and the trade is UNRESOLVED.

## 4. Flags (facts about the trade; they never change a fill)
- `late`: the alert reached the bot after the official entry minute had opened.
- `halt`: a fill had to wait for a trade (section 3, last paragraph).
- `ssr`: the short-sale price test was in force at the entry (the stock had traded 10% or more below the prior close
  that day, or on the prior session). A market short cannot execute at the bid then: the entry is UNRESOLVED.
- `no_borrow`: IBKR's list showed no shares, or fewer than the trade needed. `borrow_conflict`: IBKR listed shares
  and Alpaca said not shortable.
- `wide`: (ask - bid) / mid above 10% at a fill. `thin`: the quote's displayed size was below the order's shares at
  a fill (only the top of the book is known, so the price is not adjusted).
- `no_data`: quotes or trades could not be fetched.

## 5. How results are reported
| Label | Meaning |
|---|---|
| VERIFIED | observed: times of posting, receipt and decision; the quotes and trades on the tape |
| MODELED | the fills of section 3 at L = 1 s. The baseline. An estimate: latency, queue position and depth are assumed |
| STRESS 1 | the same with L = 5 s |
| STRESS 2 | stops only, at the extreme of the stop's minute (a short's stop at that minute's high, a long's at its low): the candle-low figure of the audit. A bound, not an estimate |
| UNRESOLVED | `ssr`, `no_borrow`, `no_data`, or an exit with no trade: shown trade by trade, kept OUT of the modeled average and reported next to it |

Two populations, never mixed: (a) the trades the paper books took; (b) for the short only, every alert of the forward
record with a stored receipt time (from 2026-11-04), entered as the rule would with the alert known only at its
receipt: decision = the later of the receipt and the start of the tested minute, still within 5 minutes of posting,
price $1 to 50 at the fill, entry 09:30 to 15:44 ET. (b) is the re-score the owner chose on 2026-11-21 (decision 1 A).

## 6. What decides what (fixed now)
- This is a re-pricing of trades already made, not a test of a rule: it has no pass or fail and enables nothing.
- From the next session the MODELED figure is computed nightly for every trade and is the figure by which a book is
  judged for continuing, pausing or retiring (criteria in the phase 2 report). The original stays visible.
- Not done: no tuning of L or of any rule on these results; no split by alert type, time or price; no second run on
  the same trades with other parameters beyond the two stress scenarios named above.

## 7. Known limits
One second is a guess. Displayed size is the top of the book at one instant. A stop that rests at a broker triggers
on that broker's rule (last sale or quote), here last sale. Halts are inferred from the tape, not read from an
official halt feed. Borrow is a list. None of this is a fill from a broker.

---
Amendments (dated, after the freeze):

**Amendment 1, 2026-11-21, after the first results were read. Flags and labels only; no fill and no price changed.**
- `ssr`: the first run read today's 10% trigger from the bars of every segment, pre-market included. The listing
  market reads it from regular-hours trades. Recomputed with regular-hours lows: 7 of the 29 shorts carry the flag
  instead of 8 (RETO 2026-11-12 loses it), 25 of the 77 forward alerts instead of 28. Most flags come from the
  carry-over day (the stock fell 10% or more on the prior session), not from the day of the trade.
- A long whose ask at the booking was already at or under its stop is labelled NO TRADE (section 3 says so); the
  first output had filed it under UNRESOLVED.
- Plumbing, no effect on a result: the first full run stopped on a column-name clash before printing anything (no
  result was seen); the run that produced the ledger is the second.
- The first results under the freeze are kept as produced (`reports/exec_model/result_2026-11-21.json`); the amended
  flags are applied to a copy.

**Amendment 2, 2026-11-21, from the independent code review. No reported number changed.**
- `ssr`, the day's own trigger: the code read the low of the order's own minute, part of which trades after the
  order. It now reads only minutes completed before the order. Re-checked on the tape for all 29 shorts and 77
  forward alerts: no flag and no label changes.
- A stop or target trigger is a trade strictly AFTER the entry fill, as section 3 words it; the code had allowed a
  trade at the same instant.

