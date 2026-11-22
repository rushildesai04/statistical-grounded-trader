# Pre-registration: what is a short under the price test worth at the only price it may be sold? (variant `short-ssr-limit-v1`, 2026-11-22)

Status: REGISTERED before any outcome of this variant is computed, on any data; no code for it exists yet. The
independent review at the end was done BEFORE the freeze and changed the plan: this is now a one-time labelled
MEASUREMENT, not a test that can add anything to the baseline.

It is an execution variant of the frozen fade, not a trading rule: same alerts, same gates, same stop, same hold. It
changes only how one class of short is assumed to be sold. It has its own identifier, its own ledger file
(`reports/exec_model/variants/short-ssr-limit-v1.jsonl`) and its own lines in the decision record.

## Problem, and its exact reach
Under the short-sale price test (the stock fell 10% or more from its prior close that day or the day before) a short
may not be sold at the bid, so the baseline's assumed market sale is not a legal order and the trade is left
UNRESOLVED: 7 of 29 taken shorts, 23 of 73 forward events; 35% of the training fades carry the flag. This variant
prices THAT class only. The shorts that are unresolved for lack of listed shares (about 13 forward events with that
flag alone) need a real locate and stay unpriceable whatever this shows.

## What argues against it, stated first
- Adverse selection. An offer is lifted when buyers push the stock up, which is when a fade is wrong; the alerts that
  simply fall never fill. A sell limit 2 to 10% above the first print, for all alerts, was tested and rejected
  (`prereg_fade_limit_entry_2026-11-17`). This is a different order (at the offer, only where a market sale is not
  allowed) but the same mechanism.
- Registered after a look. The class was chosen after 23 flagged forward events were seen at +10.7% (bar-priced, as
  if sold at a trade price). The registered check on the training file found no difference by this flag (NOT SHOWN).
  So there is no evidence these shorts are better than others, and the fill rule below cannot reproduce that +10.7%:
  it came from stocks that fell, which is exactly when a limit at the offer does not fill.
- About 30 events on about 15 sessions, with single trades of +30% and -22%: far too little to establish anything.
  This is why the plan below measures and labels, and accepts nothing.

## Population (no new choices)
The forward events of the frozen fade (`alert_fade_forward.forward_events`: a tested kind of alert, first of its stock
that day), sessions from 2026-11-23, that carry the baseline's `ssr` flag at the decision and are a rule-conforming
event at that timing (price $1 to 50, 09:30 to 15:44, within 5 minutes). The first 30 such events in order. The
`no_borrow` flag is recorded and reported; it does not remove an event.

## The order, exactly
- Decision: the later of the alert's receipt and the start of the tested minute (the baseline's forward timing).
- At decision + 1 s: read the standing best bid and ask. No usable quote (none, zero, ask at or under bid): no order;
  the event is "no quote" and counts as not filled.
- A sell-short LIMIT at that ask (above the bid, so allowed under the price test), good for 60 seconds.
- FILLED at the limit price at the time of the first official trade STRICTLY ABOVE it inside those 60 seconds (a trade
  through our price; the convention of the two earlier limit studies, conservative for queue position). A trade AT the
  limit is not a fill.
- Not filled: cancelled, no trade.
- From a fill: the frozen fade. Buy stop 20% above the fill, triggered by the first trade at or above it, filled at the
  ask one second later and never better than the level; otherwise a market buy 60 minutes after the fill (or 60 s
  before the close) at the ask. A cover is a purchase: the price test does not restrict it. Locate 0.5%.

## What is reported, once
Read ONE time, at the 30th event or on 2027-02-26, whichever is first. Until then the variant's numbers are sealed:
not in the nightly summary, not on the site, not looked at (as the 300-alert forward test is sealed).
- Fill rate, and the number of fills.
- Net per FILLED trade.
- Net per EVENT with an unfilled event at 0.
- Net per EVENT with an unfilled event at its bar-priced would-have, so the winners the order never caught are visible
  next to the squeezes it did catch.
- Each with a 95% one-sided lower bound: the 5th percentile of the means of 10,000 resamples of whole sessions (seed
  20261123). Share stopped, worst trade, the share of the total made by the best three trades, spread at the order.
- One labelled sensitivity, never the result: a fill at a trade AT or above the limit.

## What the result is used for
A permanent label on this class in every report: "not sellable as the baseline assumes; at a limit at the offer:
filled N of 30, X% per filled trade, Y% per alert". Nothing else.
- Price-test shorts are NOT relabelled MODELED, do NOT enter the baseline's average, and are NEVER counted in the
  registered review of the short book (the first 50 unflagged shorts) or in the registered 300-alert forward test.
- No gate, size or order of the live paper book changes on any outcome.
- A follow-up experiment may be PROPOSED (its own registration, the owner's decision) only if at least 15 of the 30
  filled AND the per-event figure with unfilled at 0 has its lower bound above 0. Otherwise the class stays labelled
  as above and the question is closed.

Abandon without a result if the price-test flag itself is shown wrong (for example against an exchange's own list of
restricted stocks, if one becomes available): the population would not be what it claims.

## Operation
The variant is computed after the baseline's nightly append, in its own step: a failure of the variant can never stop
or change the baseline ledger. It writes only its own file.

## Exploratory, not the measurement
Once the code exists and is reviewed, it is run ONCE on the 23 flagged forward events of 2026-11-04 to 11-20, to check
the plumbing. Those events were already seen bar-priced; that run is labelled exploratory, changes no parameter above
and decides nothing.

## Not done
No other limit price (bid plus a tick, mid), no other timeout, no market-if-unfilled fallback, no change to which
alerts are taken, no use on unflagged shorts, no re-run. A second variant would need its own registration.

---
## Independent review (before the freeze), and what was done with each point
Graded "minor gaps", nothing blocking. Found nothing in: look-ahead (the flag reads completed minutes and prior days;
fill, stop and cover are all after the decision), legality (a short limit at the ask is above the bid), the fill
convention (conservative), consistency of the outcome rules, computability from the existing model.

| Reviewer's point | Agreed? | Done |
|---|---|---|
| The accept bar was weak for about 15 session clusters with heavy tails: one +31% fill could accept a class the backtest calls noise | Yes | The accept / reject test is removed. The variant is a one-time labelled measurement; bounds are 95% one-sided; a follow-up may only be proposed at 15 fills AND a per-event lower bound above 0 |
| "Enters the baseline as its own class" would contaminate the registered 50-short review | Yes | Stated: never relabelled MODELED, never in the baseline average, never in either registered sample |
| Variant numbers on the nightly line would be a result read early; a variant crash could stop the baseline append | Yes | Sealed until the read point; computed after the baseline in its own isolated step |
| Registered after a look at 23 events at +10.7% | Yes, disclosed | Stated above, with the fact that the fill rule cannot reproduce that figure |
| The plan overstated its reach: it covers the price-test class, not the whole unresolved third | Yes | Corrected here and in `funnel_audit_2026-11-22.md` |
| Fill must be a trade strictly above the limit (the existing helper uses at-or-above); unfilled = 0 hides the winners that never filled | Yes | Strict "above" written into the order; the per-event figure is reported both ways |
| Simpler alternative: label these events "not sellable as assumed" forever and price them once at the offer | Yes: adopted | This is now the plan |

No disagreement remained. Agreement from a reviewer is not evidence that anything makes money.
