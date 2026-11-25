# Long stops seen late, and long entries booked at a stale reference

**Date:** 2026-11-25. Owner: "BNAI's stop was detected 2 minutes 47 seconds after the full tape crossed it. 'No
additional loss on this trade' does not resolve that mechanism." Paper only. Nothing in this document is active:
section 3 is a proposal, section 4 a blocker that stands whatever is decided, section 6 a decision.

## 1. The mechanism

The long book looks every 15 seconds, and each look reads the free feed's CLOSED one-minute candles
(`long_book._advance`). A stop is seen when a candle whose low is at or under it has closed. Two delays stack:

1. the free feed must print at or under the stop at all (it carries about 1% of the volume);
2. that candle must close, and the next look must come.

The nightly line "long book N checks, 100% within 16 s" measured how often the book looks. It never measured how
soon a stop is seen, and the short book's figures say nothing about it. That line now says so.

## 2. Measured: 15 past long stop exits (runner, read-only, 2026-11-25)

Seconds after the official tape's first print at or under the stop (`scripts/long_stop_lag.py`: the tape's and
the free feed's trade lists per trade). Development data: these trades were already reviewed.

| | Trades | Median | Longest |
|---|---|---|---|
| Free feed's first print at or under the stop | 13 of 15 | 34 s | 1,262 s (FCUV 11-13); 793 s (RETO 11-12) |
| No free-feed print at or under the stop in the window | 2 of 15 | | |
| The candle that showed it closed (the earliest the book could see it) | 15 | 39 s | 1,310 s |
| BNAI 11-25 | | feed print 103 s, candle close 160 s, decided 167 s | |

Four of the fifteen closed before the tape's first print after the booking: they were stopped inside their entry
candle (the one-minute setups traded until 2026-11-19).

## 3. Proposal (not built): enforce the existing stop between candles

**Is there a current-price path?** Yes: the free feed's last trade, the same request the short book polls every 10
seconds (`iex_quote`). No other timely source exists: the official feed refuses data younger than about 15 minutes.

**What would change, and what would not.**

| | Now | Proposed |
|---|---|---|
| Stop LEVEL | the setup's stop | the same: untouched |
| Detection | when a free-feed candle with a low at or under the stop has closed | also at each 15 s look: the free feed's last trade, if it printed after the entry and is at or under the stop |
| Live estimated exit price | the stop, or the candle's open when it opened under it | the lower of the stop and that last trade |
| Target, time exit, red-candle exit | on closed candles | unchanged |
| Settled replay, execution model | official bars / real quotes | unchanged |

**Difference from the registered simulation, disclosed.** The registered simulator decides on bars: a bar whose low
is at or under the stop is a stop, and the stop is checked before the target inside a bar. A print at or under the
stop makes its bar's low at or under the stop, so the proposed check can only reach the same decision EARLIER, never
a different one; the stop-before-target order is kept because the target is still read from closed candles. What
does change is the live estimate's exit time and, when the print is under the stop, its price. The live estimate is
not a registered figure; the settled replay stays the registered one.

**Failure handling.**
- A failed, missing or malformed quote: no decision from it; the candle path runs as now. Fail closed means "do not
  exit on bad data", and the candle check remains the backstop.
- A print older than the entry, or older than the last look: ignored (the quote's `since`).
- An odd lot: does not move the feed's last trade; nothing to do.
- A stale print after a large move (a likely halt): the existing guard returns no price.
- State: the same single guarded transition as today (`open` to `est_closed`), so two paths cannot close one long
  twice or send two texts.
- Cost: one request per OPEN long per look, on the long book's own connection, 5 s timeout, inside the exit sweep
  that already runs first in every pass.

**Tests it would need.** A print at the stop closes the long at once; one tick above does not; a print under the stop
books the print; a quote failure leaves the candle path intact; a print older than the entry is ignored; the candle
path closing first makes the tick path a no-op (and the reverse); target and time exits unchanged; the exit text
shows the stop, the estimated exit and the points beyond it.

**What it buys, honestly.** At most one candle: the wait for the candle to close (0 to 60 s) plus one look. On BNAI
the stop would have been seen about 50 s sooner, still about 2 minutes after the tape. On FCUV and RETO it would
have changed nothing: the free feed printed nothing near the stop for 13 and 21 minutes.

## 4. Deployment blocker (stands whether or not section 3 is built)

**The bot's own detection cannot protect a long.** The only timely data it has is a thin feed that showed the stop
a median 34 s after the tape, twice not for many minutes and twice not at all. No change of polling frequency fixes
a print that is not there. For any real long, the stop must rest at the broker, which triggers on the full tape;
the bot's detection is then a record, not the protection. Until a real-time official source exists (broker market
data), long-exit protection is NOT established, for paper results as for anything else: the live estimate of a
stopped long can be minutes late and is corrected only by the official-tape check (about 16 minutes later) and the
nightly settle.

## 5. The long's booked price is a reference, not a fill

BNAI was booked at 8.255, its break candle's close, 13.6 s after that candle closed; the quote was 8.34 x 8.39.

**What the entry rule defines** (`long_book.at_close`, owner's decision of 2026-11-20,
`long_entry_timing_2026-11-20.md`): the break is bought at the candle's close when the book sees it within 90
seconds; not when the feed already shows a later candle, when another long order of the alert is pending, when the
reference is at or under the stop, or when the 2R target cannot beat the estimated cost. A fill older than 5 minutes
is "missed". **There is no maximum price and no rule that the setup is void when the market has moved away from the
close.** So there is nothing existing to enforce; a slippage limit would be a new rule.

**Done (labels only):** each long row keeps what its booked price is (`entry_ref`: its basis, the time it refers to,
its age when booked, and the free feed's last trade at that moment), and the entry text says "a reference price,
not a fill" with those numbers. The registered replay is untouched. The execution model already prices the entry
from the standing quote one second after the decision and flags a quote smaller than the order; on BNAI it bought
8.39.

## 6. Decisions for the owner

**A. Between-candle stop check (section 3).** Build it, or leave detection on closed candles. Either way the blocker
of section 4 stands. Recommended: build it. It enforces the existing stop sooner with no change of level and no
change to any registered figure; the cost is one request per open long per look.

**B. The long's live booked price.** Keep the candle's close (the 2026-11-20 rule, now labelled), or book the live
estimate at the free feed's last trade when that trade is newer than the candle's close. The second changes only
the live estimate and the text, not eligibility and not the replay. A price LIMIT (skip when the market has moved
more than some amount) would be a new entry rule and needs its own registered test first; none is proposed here.
