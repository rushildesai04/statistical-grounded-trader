# Proposed amendment: exact decimal price thresholds (`price-decimal-v1`)

**Date:** 2026-11-25. **Status: PROPOSED. Not adopted, not in use.** Owner, 2026-11-25: "I do not approve leaving
binary rounding as the permanent definition. Prepare a separate, narrowly scoped correction... Preserve the frozen
registered replay. Give the corrected implementation a separate version... prepare a dated amendment with explicit
cohort treatment before changing registered calculations. No silent replacement or mixing versions."

Until the owner chooses a cohort treatment (section 6) every registered calculation, every live check and every
report runs exactly as before. This document, `src/sgt/fast/exact_price.py`, its tests and
`scripts/stop_arithmetic_audit.py` are the whole change: a prepared implementation with no caller.

## 1. The defect (`binary-v0`, the registered arithmetic)

Every price threshold is computed in binary floating point and compared with `>=` or `<=`:

| Threshold | As written | Example where an exact touch is missed |
|---|---|---|
| a short's stop | `high >= entry * (1 + 0.20)` | entry 5.15: the product is 6.180000000000001, so a high of 6.18 is not a stop |
| a long's target | `high >= entry + 2 * (entry - stop)` | entry 1.10, stop 1.065: 1.1700000000000004, so a high of 1.17 is not the target |
| the short-sale price test | `low <= 0.9 * prior_close` | prior close 0.70: 0.63 computes to 0.6299999999999999, so a low of 0.63 does not trigger |

The rules say "at or above" and "10% or more". Whether a touch counts depends on which side of the true level the
nearest double happens to fall. Away from the exact touch the two arithmetics always agree (tested: one sub-penny
either side of every level on the five-cent entry grid).

## 2. The corrected definition (`price-decimal-v1`)

- **A price is the decimal number its source reported.** Market data arrives as JSON numbers; the shortest decimal
  that round-trips the stored double is exactly the number sent, for any price of up to 15 significant digits.
- **Precision policy.** A reported price has at most 6 decimal places. Quotes move in $0.01 at $1.00 and above and
  in $0.0001 below (Regulation NMS Rule 612); trades print inside those ticks, so the bound is not 2 places. On our
  stored bars the prices compared carry 0 to 4 places, with 7 values of 5 or 6 places among 15,392 (section 4). A
  number needing more than 6 places is refused as not a price. It is never rounded into one.
- **A threshold is exact and is never rounded.** 20% above 1.9692 is 2.36304. No quote can sit there; the first print
  at or above it is 2.3631. Rounding the level to a cent would move it by up to half a cent with no basis in the rule.
- **Comparison.** `price >= level` and `price <= level` on exact decimals. No tolerance anywhere. The arithmetic
  runs in a context that raises if any step would round, so exactness is enforced rather than assumed.
- **Rates are exact decimals** (`Decimal("0.20")`); a float rate is refused, since 0.2 as a double is not 0.2.

## 3. Where the same comparison occurs

| Record | Code | Registered? | Class |
|---|---|---|---|
| Fade settle and forward record (the rule replay) | `scripts/alert_fade_test.cover`, `sgt.fast.would_have.cover` | yes (`prereg_alert_fade_2026-11-01`) | short stop |
| Long settle, long shadow | `sgt.fast.patterns.simulate` | yes (long registrations) | long target |
| Execution baseline (the cohort from 2026-11-23) | `sgt.fast.exec_model` (frozen v1 of that model) | yes (`exec_baseline_2026-11-21`) | short stop, long target, price-test flag |
| Live paper checks | `fade._exit`, `long_book._exit`, `scanner.step`, `tape_check`, `long_tape_check`, `text_order`, `text_px` | no (the paper book's behaviour) | short stop, long target |
| Labels read back from settled numbers | `charts.official_short_exit`, `fade.settled_exit` (each uses a `1e-9` tolerance) | no | follow the version that produced the record |
| Not a price threshold, same hazard, already corrected in this change | `revisions.large` (a `1e-12` tolerance on the 5-point rule) | no (a report) | now exact at a stated 6-place precision |
| Not price thresholds, not changed | `fade.caps` (`+ 1e-9` on a weight sum), `book.EPS` | no | sums of weights, no market price |
| Registered, never implemented | drift monitor amendment A1 ("gross <= -0.20 + 1e-9") | inactive by owner decision | to be restated exactly if it is ever built |

## 4. Measured on the stored history (runner, read-only, 2026-11-25 00:4x ET)

`scripts/stop_arithmetic_audit.py`. Counts of decisions; no return was ranked, averaged or used to choose anything.

| Population | Trades | Bar comparisons | Exact touches | Trades decided differently |
|---|---|---|---|---|
| P1 settled live shorts, registered replay | 34 | 1,786 | 1 (GLXG 11-24) | 0 |
| P2 the same trades at the live entry level | 34 | 1,587 | 0 | 0 |
| P3 every alert with stored bars, forward-record entry | 126 | 6,678 | 1 | 0 |
| P4 settled longs, target | 26 | 717 | 4 | **1** |
| P5 ledger rows, price test | 34 | 68 | 0 | 0 |
| P6 long shadow, target | 163 | 4,556 | 31 | **3** |

No value was refused as not a price. Not measured: the execution model's thresholds against the tick tape (its
history run is frozen and re-fetching the tape costs data budget), and the research history files (single-run
studies, not re-read).

**The trades that differ, both outcomes shown:**

| Trade | `binary-v0` (registered, stands) | `price-decimal-v1` |
|---|---|---|
| Long 43, RAIN 2026-11-17 (before the cohort; entry 1.10, stop 1.065, target 1.17) | target never reached (highs of 1.17 at 11:45 and 11:50 are under 1.1700000000000004); stopped 1.065 at 11:56; -3.18% before costs, -3.92% net | the target is reached in the fill minute. NOT a verified outcome: see the replay below |
| Shadow 41, RAYA 2026-11-06 | target first reached 09:51 | 09:50 |
| Shadow 142, XLAB 2026-11-12 | target first reached 11:55 | 11:54 |
| Shadow 218, GMM 2026-11-18 | target first reached 11:36 | 11:30 |

**RAIN replayed on the full tape (2026-11-25, owner: "an exact target touch alone does not establish that it
occurred after entry and before the stop").**

| Question | What the tape shows | Established? |
|---|---|---|
| Did the touch come after the entry trigger? | first print at or above the 1.10 trigger 11:45:00.327; first print at 1.17 11:45:08.640 | yes: 8.3 s after |
| Did it come before the stop? | first print at or under 1.065 after the trigger: 11:56:13.809 (1.06) | yes |
| Was the entry price 1.10 available for the order (about 3,300 shares)? | the minute opened with a sweep from 1.0701 to 1.15 in 18 ms (181 prints, 34,487 shares); 975 shares printed at or under 1.10; the offer at the trigger print was 300 shares at 1.10, a second later 300 at 1.12 | no. The registered simulator books a stop entry at its trigger; that is its convention, not a fill |
| Would a 3,300-share limit at 1.17 have filled? | 260 shares printed at 1.17 at 11:45:08 against 1,800 offered there; about 8,400 more at 1.17 from 11:50:37 to 11:50:48; one 40-share odd lot above it (1.1783) | not established: it depends on the order's place in the queue |

So the two versions differ on one fact only: whether the 1.17 high counts as reaching the 1.17 target. Under the
registered simulator's own conventions (entry at the trigger, a touch is a fill) `price-decimal-v1` reads "target".
That is a rule-replay reading, not a corrected result, and no figure is put on it here: the parallel calculation
will produce v1 figures for new trades only (option A; history is not restated). The third record of this trade
says more about what was achievable: the execution model, from the bot's own decision at 11:46:15 on real quotes,
bought 1.15 and was stopped at 1.05, -8.7% (flag: quote smaller than the order).

For the three shadow rows only the minute was compared; their full results under v1 are not computed (it needs the
simulator, not built). The direction is set by the rule, not by the result: v1 adds stops to shorts and price-test
flags (worse for a short, or an exclusion) and adds targets to longs (better for a long). One long whose reading
changes is not a reason for anything; the long book's tested record is unchanged by it.

## 5. What a correction would touch

Wiring v1 means replacing the three expressions of section 1 in the registered replays, the frozen execution model
and the live checks, each behind its version name, plus a `v1` simulator for longs. None of that is in this change.

## 6. Cohort treatment: the decision

| Option | What happens | For | Against |
|---|---|---|---|
| **A. Beside, not instead (recommended)** | `binary-v0` stays the registered calculation of every record and cohort now running, to its registered end (the execution baseline through its review at 50 / 30 or 2027-02-26; the fade forward record F1 to its decision; the long records). From an effective date `price-decimal-v1` is computed beside it for every new trade and stored in its own versioned fields. Reports show one line: how many trades the versions decide differently, with both outcomes. No history is restated. A record adopts v1 as its registered calculation only when it is next registered (a new cohort), never part-way | no cohort mixes versions; nothing registered is replaced; every difference is visible as it happens | two figures exist for the rare trade that differs; the registered one keeps a known arithmetic flaw until the cohort ends |
| B. Switch from a date | every registered calculation uses v1 from the effective date; cohorts in progress are labelled as mixed and carry both figures for every trade | the flaw stops deciding trades at once | a cohort in progress then holds trades scored two ways: exactly the mixing you ruled out, unless it is also restated |
| C. Restate everything | all history and all cohorts recomputed under v1, v0 archived | one arithmetic everywhere | rewrites registered results, RAIN included; needs the long simulator and a tape re-fetch for the model; the largest change for a difference in 1 of 60 settled trades |

**Recommendation: A**, with the live paper checks left on `binary-v0` for as long as the record that settles them is
(so the live path and its settle never disagree on a touch), and the effective date set when the wiring is reviewed
and merged. It keeps your two conditions (no silent replacement, no mixed cohort) and still stops the flaw from
being the permanent definition: v1 is the definition of every record registered from now on.

## 7. What is needed from the owner

1. A, B or C.
2. Whether the live paper checks move with their record (recommended) or at once.
3. Go-ahead to wire it: versioned fields, the v1 long simulator, the nightly "versions differ" line, tests. Estimated
   one working session plus a review; it touches registered replay code, so it gets an independent review before merge.
