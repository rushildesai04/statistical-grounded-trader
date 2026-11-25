# Live short entries: decided inside the registered minute, else record-only (`live-entry-v2`)

**Date:** 2026-11-25. **Owner's decision (verbatim):** "Substitute short entries are record-only. If the registered
entry opportunity has already passed, a later free-feed print does not authorize a replacement trade. This decision
follows the specification, not the outcomes of BUUU or GLXG."

Paper only. The registered replay, the forward record, the frozen cohort and every stored trade are unchanged. No
already-observed trade is removed: BUUU 2026-11-23 and GLXG 2026-11-24 stay in the record as they are.

## 1. The specification

`prereg_alert_fade_2026-11-01.md`, frozen rule: "Entry time and price exactly as in that test": ONE bar, the first
official 1-minute bar at or after alert + 10 s (`telegram_alert_test.py:85`). The five minutes
(`telegram_alert_test.py:86`) bound how late that FIRST bar may be (a halt, no prints); they are not a window in
which a later bar is an entry. Amendment F5: "every qualifying event enters at the tested timing".

| | What it is | Registered? |
|---|---|---|
| Waiting for the first qualifying print | the official tape has not traded since alert + 10 s; the first bar, when it comes within five minutes, IS the entry | yes |
| A substitute entry | the official tape already traded in an earlier minute; a later print is taken instead | no: now record-only |

## 2. What can be known at the moment of the entry

Checked on the runner, read-only, 2026-11-25 00:5x ET.

| Question | Source that is timely | Answer |
|---|---|---|
| Did the stock trade on the official tape in the last 15 minutes? | none. The official feed answers `403 subscription does not permit querying recent SIP data`; it is readable from about 16 minutes back | **cannot be known in time** |
| Did it trade EARLIER, since its registered minute began? | the free feed's own trade list, odd lots included (its "latest trade" moves only on round lots) | **yes can be confirmed**: any trade there is also on the official tape |
| Did it NOT trade (a halt, a genuinely delayed first print)? | none. The free feed carries a small share of a stock's trades; its silence is not the tape's | **cannot be confirmed**; never inferred |

The two substitute entries show both sides. GLXG 2026-11-24: the free feed's trade list has three odd lots between
12:58:56 and 12:59:56, before the round-lot print the book used at 13:00:29: earlier trading is confirmed. BUUU
2026-11-23: the free feed shows no trade at all from 10:43:00 to 10:45:00 ET, while the official tape traded in the
registered minute 10:43: silence there would have been wrong evidence of "no trading".

## 3. The rule (`fade._enter`, version `live-entry-v2 2026-11-25`)

A live short entry is decided inside its registered entry minute (from the start of the first minute at or after
alert + 10 s, for 60 seconds) or not at all. Applied at the last step, to an alert that passed every other check
and would have been shorted:

| At the moment the price is there | Outcome | Recorded |
|---|---|---|
| inside the registered minute | entered, as before | `entry_elig` "inside the registered entry minute" |
| later, and the free feed shows an earlier trade | recorded, not shorted, no text | "substitute", the trade's time, size and price; the print not used |
| later, and nothing confirms earlier trading (silent feed, no list, a failed request) | recorded, not shorted, no text | "unknown", with the statement that a halt or an untraded minute is NOT confirmed |

Every row carries `entry_rule` (the version) and `entry_evidence`. The same night the official bars are read and
`entry_official` says what they showed: traded in the registered minute, first traded in a later minute, or no bar
within five minutes. That is how the cost of "unknown" is counted, never guessed.

The recorded alert stays an alert: the 7-day repeat rule counts it, the long book may trade it, the would-have
record scores it from official bars, the site lists it with its reason, and the nightly counts them.

## 4. What is unchanged, and what "genuinely delayed first trading" now means for the live book

- **The registered handling is preserved where it is computed:** the settle, the forward record and the would-have
  record all read official bars and enter at the first bar within five minutes, a delayed first bar included.
- **The live book cannot confirm a delay in time (section 2), so by the owner's rule it labels the case unknown and
  does not enter.** On the history this costs no trade: of 34 live short entries none was a confirmed delayed first
  print (30 inside the minute; 2 approved / auto entries of the withdrawn ask-first mode; 2 substitutes).
- An alert with no free-feed print for five minutes: skipped with the same reason as before (not an event).
- Approved and auto entries of the ask-first mode (off since F5): their own registered timing, untouched.
- Restart recovery: the rules of 2026-11-22/23, untouched.
- **Delivery delay: decided the same day.** Owner, 2026-11-25: "Delivery-late short alerts are record-only under the
  same registered-minute eligibility principle." An alert that itself reaches the book after its registered minute
  has ended is recorded, not shorted, and its evidence starts with when it arrived and how late. (First written
  here as "still open".) Of 422 alert rows 3 arrived more than 60 s after posting; no live entry was of this kind.

## 5. Not built: a timely source that could confirm a halt

Entering a genuinely delayed first print live needs a timely official source: the listing markets' public halt feed
(free; a new outside dependency to parse and bound) or real-time official data (paid; ruled out until a book is
proven). Owner, 2026-11-25: "Do not add a halt-feed dependency now." Neither is built; "unknown" stays record-only.

## 6. The registered cohort (execution baseline, from 2026-11-23)

Its definition is unchanged: the trades the book enters, priced from the bot's own decision. From this version the
book enters no substitute, so none joins the sample; the two already in its sessions stay (BUUU flagged and not
counted since 11-23, GLXG counted). Nothing is restated and the criteria are untouched.
