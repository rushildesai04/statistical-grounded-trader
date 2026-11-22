# Pre-registration: can a short under the price test be sold with a limit at the offer? (variant `short-ssr-limit-v1`, 2026-11-22)

Status: REGISTERED before any outcome of this variant is computed, on any data; no code for it exists yet. The
independent review at the end was done before the freeze: its accepted points are already in the text. It is an
EXECUTION variant of the frozen fade, not a new trading rule: same alerts, same gates, same stop, same hold. It
changes only how one class of short is assumed to be sold. It has its own identifier, its own ledger file
(`reports/exec_model/variants/short-ssr-limit-v1.jsonl`) and its own lines in the decision record; it reads the
baseline's alerts and writes nothing to the baseline's ledger or review.

## Problem
The execution baseline cannot price a third of the short book. Under the short-sale price test (the stock fell 10% or
more from its prior close that day or the day before) a short may not be sold at the bid, so the baseline's market
sale is not a legal order and the trade is left UNRESOLVED: 7 of 29 taken shorts, 23 of 73 forward events. In the
training backtest 35% of fades carry the flag. A baseline that silently drops a third of its events is incomplete.

## What argues against it, stated first
- Adverse selection: an offer is lifted when buyers are pushing the stock up, which is when a fade is wrong. A sell
  limit 2 to 10% above the first print, for all alerts, was tested and rejected (`prereg_fade_limit_entry_2026-11-17`).
  This variant is a different order (at the offer, only where a market sale is not allowed), but the mechanism is
  the same and may sink it.
- The flagged alerts did well in three live weeks (+10.7% on 23, bar-priced as if sold at a trade price) and NOT in
  the backtest (+1.37% on 467 against +1.76% unflagged; registered check, NOT SHOWN). There is no evidence these
  shorts are better than others. The question here is only whether they can be sold at all, and at what price.
- 30 events is a small sample; days are correlated.

## Population (no new choices)
The forward events of the frozen fade (`alert_fade_forward.forward_events`: a tested kind of alert, first of its stock
that day, as the nightly forward record already builds them), sessions from 2026-11-23, that carry the baseline's
`ssr` flag at the decision and are a rule-conforming event at that timing (price $1 to 50, 09:30 to 15:44, within 5
minutes). The first 30 such events in order. The `no_borrow` flag is recorded and reported; it does not remove an
event (the question is the order, and most of these alerts never get a borrow stamp).

## The order, exactly
- Decision: as the baseline's forward population: the later of the alert's receipt and the start of the tested minute.
- At decision + 1 s: read the standing best bid and ask. No usable quote (none, zero, ask at or under bid): no order,
  the event is "no quote" and counts as not filled.
- A sell-short LIMIT at that ask, good for 60 seconds.
- FILLED at the limit price at the time of the first official trade STRICTLY ABOVE it inside those 60 seconds (a trade
  through our price; the convention of the two earlier limit studies, conservative for queue position).
- Not filled: cancelled. No trade. The event counts as 0 in the per-alert figure.
- From a fill: the frozen fade. Buy stop 20% above the fill, triggered by the first trade at or above it, filled at the
  ask one second later and never better than the level; otherwise a market buy 60 minutes after the fill (or 60 s
  before the close) at the ask. A cover is a purchase: the price test does not restrict it. Locate 0.5%.

## What is reported
Fill rate. Net per FILLED trade and net per EVENT (unfilled = 0), each with the lower bound used by the baseline
review (10th percentile of the means of 10,000 resamples of whole sessions, seed 20261123). Share stopped, worst
trade, the share of the total made by the best three trades, spread at the order. One labelled sensitivity, never the
result: a fill at a trade AT or above the limit.

## Decision (read once, at the 30th event or on 2027-02-26, whichever is first)
| Outcome | Condition | Consequence |
|---|---|---|
| EXECUTABLE | fill rate at least 50% AND net per event above 0 AND its lower bound above -2% | price-test shorts enter the paper baseline as their own class, priced this way. Nothing changes in the live paper book or in any gate |
| NOT EXECUTABLE | fill rate under 25% OR net per event at or under 0 | they stay excluded from the baseline for good; this question is closed |
| INCONCLUSIVE | anything else | extended once to the first 60 events or 2027-02-26; then EXECUTABLE or NOT ESTABLISHED (excluded) |

Abandon without a result if the price-test flag itself is shown wrong (for example against an exchange's own list of
restricted stocks, if one becomes available): the population would not be what it claims.

## Exploratory, not the evaluation
Once the code exists and is reviewed, it is run ONCE on the 23 flagged forward events of 2026-11-04 to 11-20, to check
the plumbing and to see a fill rate. Those events were already seen bar-priced, so that run can neither accept nor
reject anything, and no parameter above may change after it.

## Not done
No other limit price (bid plus a tick, mid), no other timeout, no market-if-unfilled fallback, no change to which
alerts are taken, no use on unflagged shorts, no re-run. A second variant would need its own registration.

---
## Independent review (recorded after the freeze; disagreements and what was done)
(to be filled from the reviewer's report)
