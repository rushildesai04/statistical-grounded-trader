# Live paper longs: never opened once their exit is already determined (`long-live-entry-v2`)

**Date:** 2026-11-25 (evening). **Owner's decision (verbatim):** "Trigger setup discovered after its opportunity is
over: B - record-only. Do not book a position retrospectively. Preserve the hypothetical replay separately and
document the prospective effective version. Do not remove historical observations or silently change registered
sample membership."

**Scope confirmed by the owner, 2026-11-26 (verbatim):** "Apply record-only handling to every long setup where its
existing eligibility window or trigger opportunity has demonstrably expired. Define 'already over' using each
setup's specification. Do not reject a still-valid setup merely because its qualifying candle completed earlier.
Unknown cases need an explicit reason, not retrospective booking." and "No individual text for these recorded-only
setups is fine. Keep the reason on the site and in the nightly counts, distinguishing expired, invalidated, and
insufficient-data cases." Sections 2, 2a, 3 and 5 are written to that.

**Second message, 2026-11-26 (verbatim):** "'Target already reached' may remain classified as expired. A missing
free-feed candle need not automatically invalidate a setup, but it also cannot prove that the stop or target was
untouched. Distinguish confirmed quiet intervals from unknown coverage. If entry eligibility depends on an unobserved
interval and cannot be established, record insufficient data rather than open retrospectively. Verify this without
changing the registered replay. Document the allowed 4.5-minute discovery case clearly: what establishes that the
setup remains eligible, and which current price is used? Keep historical trigger prices separate from executable
paper estimates." Sections 2b and 2c are written to that. The version was never deployed in its
earlier form, so there is one `long-live-entry-v2`, the one described here.

Paper only. A change to what the LIVE paper long book opens. The setup rules, the stop, the target, the exits, the
registered replay, the long shadow, the execution model and every stored row are unchanged. It answers question C
of `long_stop_detection_2026-11-25.md`, section 6 (option C2 there).

## 1. What was wrong

The book reads closed candles. A buy-stop that fills inside a candle is seen when that candle has closed, so the
booking always comes after the entry reference. When the same candles also settle the EXIT, the book used to open a
long and close it in the same pass, or open one whose exit was only waiting to be written. FCUV, 2026-11-25: booked
66 s after its fill candle began; that candle had closed under the entry (the adopted red-candle exit: out at the
next open). Both texts described a trade whose entry and exit were already in the past.

## 2. The rule (`long_book.already_over`, version `long-live-entry-v2 2026-11-25`)

Applied at the last step, to a long that passed every other check and would have been opened:

| On the closed candles the book holds when it sees the fill | Outcome | Recorded |
|---|---|---|
| the exit is still undetermined | opened, as before; the row's decision line carries the version | status `open` |
| a stop or a target is already on a closed candle (the fill candle included), or the time exit's window has ended | not opened, no text | status `over`, the exit that was determined, its price and candle, how long after the fill candle began it was seen |
| the fill candle has closed under the entry under rules A2 (red-candle exit) | not opened, no text | status `over`, "out at the next candle's open" |

The test is the book's own exit code (`_exit`, with the finality rule of the exit check), run on the row the book
would have written. "Over" is therefore exactly "the exit check of this same pass would have closed it, or had
nothing left to decide"; it cannot disagree with what the book would have done a moment later, and it adds no
threshold. It reads only candles already held: no request.

Order of the checks is unchanged ahead of it (missed, cost cap, our short open), so the samples of those two tested
rules keep every row they would have had. A measure-only "seen" text is sent for what the book would open, so an
over setup is not texted as seen either.

**Every setup kind, each by its own specification** (confirmed 2026-11-26). `long_book.rules_of` is the one place
that says which exits a setup has:

| Setup | Specification | Exits that can make it over |
|---|---|---|
| base break (L8) | rules B1 | its stop, its 2R target, the hold window. No red-candle exit: a red candle after the break is not a reason |
| bull flag (L5v2) and every other trigger setup | rules A2 | the same three, plus the red-candle exit (fill candle closes under the entry: out at the next open) |

A base break bought when its candle closes, the normal case, has no later candle and is never over.

## 2a. The three classes (on the row, on the site, in the nightly counts)

Every setup that is recorded and not opened carries `long_trades.not_opened` (JSON: the version, the class, the
cause in words, and the exit that was determined when there is one). The nightly settle can rewrite a row's
`reason`; this column stays as written.

| Class | When | Status | Replayed nightly |
|---|---|---|---|
| **expired** (the opportunity ran out) | the fill was more than 5 minutes old when seen (`LIVE_LAG`, the book's existing eligibility window), or its candle began before the listener was back | `missed`, as before this version | no, as before (the long shadow scores every setup anyway) |
| **expired** | the 2R target was already on a closed candle; or the hold window, or the session, had ended | `over` | yes, hypothetical |
| **invalidated** (the setup's own exit already said out) | its stop was already on a closed candle (every kind); or its fill candle closed under the entry where the specification has the red-candle exit | `over` | yes, hypothetical |
| **insufficient data** | the book's 5-minute window ended while a closed minute since the entry had no candle and none after it (section 2b): eligibility was never established | `missed`, with both facts in the cause | no, as before |
| **insufficient data** (it cannot be told) | a candle from the fill on has no usable price (not a number, zero, negative) and nothing before it settles the question; or the long's own entry, stop or target is not a usable price; or the check itself failed | `over`, with the cause in words | yes, hypothetical; a row whose stop is not a price has no order to replay: no size, target or stop is stored, and the settle says so once |

Three things this fixes in place:

- **A still-valid setup is opened.** A trigger fill is always seen after its candle has closed; that alone is never
  a reason. Seen four and a half minutes after the fill candle began, with no stop, target or red close on the
  candles since: opened, exactly as before (tested).
- **Unknown is said, never guessed.** Before, a check that failed opened nothing and tried again in the next pass,
  leaving no record; a candle with no usable low compared as "stop not reached". Now each is recorded at once as
  insufficient data with its cause, nothing is opened then or later, and it is counted every night. What the candles
  BEFORE an unusable one already settle still counts (a stop on the fill candle is invalidated, not unknown).
- **A minute with no candle is quiet only when that is confirmed** (section 2b).

Target-already-reached is classed expired (the move the setup was for has happened), not invalidated; the owner
confirmed it 2026-11-26. The mapping is one table (`long_book.CLASS_OF`).
The `missed` rows get the class from this version on; rows made before it are untouched and keep their old words.

## 2b. A confirmed quiet minute against unknown coverage (`long_book.coverage`)

The free feed has no candle for a minute in which nothing traded on its exchange. A missing candle therefore has two
possible meanings, and they look the same: nothing traded there, or the feed has not delivered that minute yet. For
every closed session minute from the entry's candle up to the moment the book looks:

| The minute | Meaning | What the book does |
|---|---|---|
| a closed candle is held | observed: the exit check reads it | as section 2 |
| no candle, and a LATER candle is held | **confirmed quiet on the free feed**: the feed had reported past that minute and showed no trade in it | the setup stays eligible. The number of such minutes is kept on the row (`entry_ref.quiet_minutes`) and said in the text. It is NOT evidence that the official tape was quiet (the free feed carries about 1% of the volume) |
| no candle and none after it | **unknown coverage**: quiet, or not delivered yet | the long is not opened in that pass. The book looks again each pass. If a later candle arrives the minute becomes confirmed quiet (or shows what happened). If the five-minute window ends first, the setup is recorded as insufficient data, with the window and the uncovered span both in the cause |

While the book is in measure-only mode the same holds for its "seen" text: it is sent for what the book would
open, so it waits for the coverage too, and is not sent for a setup that ends as insufficient data (review
2026-11-26; the books are on, so nothing changes today).

No request is made for this (it reads the frame the book already holds) and no threshold is added (the only clock
is the existing five-minute window). An exit that is already on a held candle is decided whatever is unknown after
it. A fill seen when its own candle closes, the normal case, has one closed minute, its candle: the rule changes
nothing for it (the 23 live longs measured in `long_entry_timing_2026-11-20.md` were booked 62 to 88 s after their
fill candle began, that is within 28 s of its close; how often a fill is first seen two or more minutes late has not
been measured). The registered replay is untouched: it runs on the official candles at night, as before, for opened and
recorded rows alike (tests: the settle lines and totals).

**One case is left as approved, and is the owner's to decide.** A base break is bought at its break candle's close
for up to 90 s after that close while no later candle is held (`AT_CLOSE`, owner 2026-11-20: "the
buy-at-candle-close changes only on the owner's word"). Between 60 and 90 s one closed minute has no candle, which
by the rule above is unknown coverage. Applying the rule there would end that window at 60 s. It is NOT applied: the
buy is made as approved, and the row says so (`entry_ref.unobserved_minutes`, with a note). BNAI 2026-11-25, the one
live case looked at, was booked 13.6 s after the close. To make the two consistent, say "end the at-close window at
60 s"; it is a one-line change.

## 2c. The allowed late discovery, step by step (a fill first seen 4.5 minutes after its candle began)

Example: a buy-stop at 10.55 fills inside the 10:03 candle; the book first looks at 10:07:30.

**What establishes that the setup is still eligible** (all of these, in this order; any one failing records it):

| Check | Fails as |
|---|---|
| the fill candle began no more than 5 minutes ago, and after the listener was back | expired; insufficient data where a minute since the entry was also never covered (2b) |
| estimated cost at or under the cap; our own short on the stock not open | not bought (their own existing statuses) |
| every closed candle from the fill candle on: low above the stop, high under the 2R target | invalidated / expired |
| where the specification has the red-candle exit: the fill candle closed at or above the entry | invalidated |
| every closed minute since the entry is a held candle or a confirmed quiet minute (2b) | not opened yet; insufficient data if the window ends |
| every value compared is a usable price | insufficient data |

**Which price is used.** Three different prices exist, and none is written into another's field:

| Price | What it is | Where it lives |
|---|---|---|
| 10.55, the trigger | the registered entry REFERENCE of the setup: a historical price, 270 s old in this example. It is what the paper long is booked at, as the registered replay books it; it is not a current price and not a fill | `est_entry`; the text's headline, with "that candle began 270 s before the booking. A reference price, not a fill" |
| the free feed's last trade and one exchange's offer at the booking | the CURRENT prices the book could read at 10:07:30, with their age and, for the offer, its size against the order and its distance from the reference | `entry_ref` (`last_px`, the venue fields); the same line of the text. They decide nothing and are never booked |
| the execution model's entry | the executable paper estimate: the official quote at the bot's decision time plus latency, computed at night | the nightly ledger. The registered cohort is judged on this, not on the trigger |

So a late-discovered long is booked at a stale reference by design (that is the registered convention), and how
stale, and what the market was showing at that moment, are on the row and in the text. Whether late discoveries
should be booked at a current price instead is the different entry rule of section 6 (not proposed).

## 3. What is preserved, separately

- **The hypothetical replay.** The nightly settle replays the recorded order on official candles, as it does for
  every watched row, and writes the result on the row (status stays `over`). It is labelled hypothetical, appears in
  the nightly report on its own line, and is in no total, no book value, no drift input, no ledger and no review.
- **The long shadow** scores every setup of every alert on official candles each night, as before.
- **History.** Nothing stored is changed. The six past longs that meet the definition stay live paper longs with
  their texts and their settled results (section 4).
- **The site** lists the alert with its class and cause, for example "long not opened (invalidated): its stop was
  already on a closed free-feed candle when the book saw the fill ($10.20, 10:03 ET candle)", and the replay figure
  where there is one.
- **The nightly report** has one line for the day (each class with its count, zero included, and each setup with
  its cause) and one running line: "N opened, K recorded only (a expired, b invalidated, c insufficient data)".

## 4. What it changes, stated plainly

1. **The live red-candle exit no longer occurs for new longs.** The book sees a trigger fill only after its fill
   candle has closed; if that candle closed under the entry, the long is now recorded, not opened. The exit code
   stays (longs already open at a deploy; the replay; the shadow), but from this version the live paper book holds
   no red-exit trade. A fill candle that reached the target is recorded as well: the rule removes winners and
   losers of that kind alike.
2. **The live long book is a narrower thing from this version.** It holds trigger-setup longs whose fill candle
   closed at or above the entry without reaching the stop or the target, and base breaks bought at the close. Its
   average from here on is not comparable with its average before, and neither is a measure of the rule replay
   (the settle of every row, opened or recorded, and the shadow are).
3. **Registered sample membership (execution baseline, longs from 2026-11-23).** Its definition is unchanged: the
   trades the book enters, priced from the bot's own decision on official quotes. From this version the book enters
   no already-over long, so none joins the sample; FCUV (long 89), already in its sessions, stays. Nothing is
   restated and the criteria are untouched. This is a change in which future alerts become sample members, made on
   the owner's decision and visible every night: the report says how many longs were opened and how many were
   recorded instead since the rule began, with the recorded ones' replay beside it. The same treatment the short
   book's record-only entries were given (`entry_eligibility_2026-11-25.md`, section 6).
4. **Counts on the stored history (runner, read-only, 2026-11-25; counts only, no return was read).** Of 28 live
   paper longs, 6 had their exit already determined at the booking: five one-minute-setup longs before 11-23 whose
   stop was in the fill candle (KULR 11-10, the restart case; ZDGE 11-11, SLSN 11-13, SPAI 11-17, POAS 11-19; booked
   63 to 82 s after the fill candle began, KULR 66 minutes), and FCUV 11-25, the only red exit. Under the kinds
   live since 11-20 (base break, bull flag): 1 of 4.

## 5. Texts

No new text type (confirmed by the owner 2026-11-26). A recorded setup sends nothing during the day: no position
existed. It is named, with its class and cause, in the nightly report and on the site. An "armed" text (a setup whose order is pending) can therefore be followed by no entry text;
the nightly line says what became of it.

## 6. Not done

- Acting from the booking instead (option C3: buy at the booking, judge exits from that moment) is a different entry
  rule and would need its own registration. Not proposed.
- No rule about HOW LATE a fill may be seen (the five-minute limit stands). The rule is about the exit, not the age.
