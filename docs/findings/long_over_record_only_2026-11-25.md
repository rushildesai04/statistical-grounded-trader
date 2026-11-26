# Live paper longs: never opened once their exit is already determined (`long-live-entry-v2`)

**Date:** 2026-11-25 (evening). **Owner's decision (verbatim):** "Trigger setup discovered after its opportunity is
over: B - record-only. Do not book a position retrospectively. Preserve the hypothetical replay separately and
document the prospective effective version. Do not remove historical observations or silently change registered
sample membership."

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

**Any setup kind.** The owner's words name trigger setups; the same thing happens to a base break first seen after a
later candle has closed (its buy is then a past open), and "do not book a position retrospectively" covers it. A
base break bought when its candle closes, the normal case, has no later candle and is never over. Say so if the
rule should be limited to trigger setups.

## 3. What is preserved, separately

- **The hypothetical replay.** The nightly settle replays the recorded order on official candles, as it does for
  every watched row, and writes the result on the row (status stays `over`). It is labelled hypothetical, appears in
  the nightly report on its own line, and is in no total, no book value, no drift input, no ledger and no review.
- **The long shadow** scores every setup of every alert on official candles each night, as before.
- **History.** Nothing stored is changed. The six past longs that meet the definition stay live paper longs with
  their texts and their settled results (section 4).
- **The site** lists the alert with "long not opened: already over when the book saw it" and the replay figure.

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

No new text type. An over setup sends nothing during the day: no position existed. It is named in the nightly
report and on the site. An "armed" text (a setup whose order is pending) can therefore be followed by no entry text;
the nightly line says what became of it.

## 6. Not done

- Acting from the booking instead (option C3: buy at the booking, judge exits from that moment) is a different entry
  rule and would need its own registration. Not proposed.
- No rule about HOW LATE a fill may be seen (the five-minute limit stands). The rule is about the exit, not the age.
