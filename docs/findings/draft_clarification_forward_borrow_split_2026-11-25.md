# DRAFT clarification: the forward "flagged / unflagged" split that is already registered (no new test)

**Status: DRAFT FOR THE OWNER'S REVIEW. Nothing registered, nothing computed.** Owner, 2026-11-25: "P3 must
identify overlap with the already registered borrow study and H1. Prefer a documented clarification or extension if
it asks the same question. No duplicate testing, altered thresholds, or early reading of sealed outcomes."

## 1. Finding: the proposed forward borrow split would duplicate registered work

`edge_assessment_2026-11-25.md` proposed registering a forward comparison of alerts by their borrow listing. Read
against what is already registered, it asks a question that is already being asked three times:

| Registered | Population | What it asks | Decides | Overlap |
|---|---|---|---|---|
| Executable baseline, short cohort (`exec_baseline_2026-11-21`, section 6) | shorts the live book took from 2026-11-23, priced on real quotes, NO exclusion flag | are shorts with no exclusion flag profitable as the bot could have traded them? | first 50 (early look 25), extended once | the same question for the trades that were taken |
| Amendment F1's forward record, "reported with the flagged / unflagged split" (the same section 6) | every in-cell forward alert | does the forward record's result sit in alerts that carry an exclusion flag? | reported at F1's decision (first month-end with 300); never changes F1's verdict | **the same question, the same population the proposal named** |
| `prereg_after_day_borrow_forward_2026-11-17` | forward alert days, borrowable at the close or not | the multi-day short on names that could be borrowed, with "the same trades on the NOT borrowable days" beside it | 300 borrowable alert days | the same borrow dimension on the same alerts; a different trade (close to close, 1 and 5 days) |
| H1, `short-ssr-limit-v1` (`prereg_short_price_test_limit_2026-11-22`) | the first 30 forward events under the price test | what a short under the price test is worth at the only price it may be sold | 30th event or 2027-02-26; sealed | none on borrow: "The `no_borrow` flag is recorded and reported; it does not remove an event." |
| `prereg_borrow_scan_forward_2026-11-18` | every listed stock, not the alerts | the loan list as a scanner (shares vanishing, fees jumping) | 300 events of a kind | none: a different population and question |

**So no new test is drafted.** A fourth registered comparison on the same alerts would be a duplicate, and its
result would not be independent of the other three.

## 2. What is missing instead: the registered split is not fully defined

The baseline says the forward test is "reported with the flagged / unflagged split" and stops there. Read against
the code, three things are open, and all three should be fixed before the split is ever computed on new alerts:

1. **The borrow stamp does not exist for every forward alert.** `exec_ledger.forward_rows` takes it from the bot's
   own row (`ib_avail`). The baseline says so itself: of 48 forward alerts the bot did not take, 12 "never were"
   stamped, and "the unflagged set contains shorts whose borrow is simply unknown".
2. **Which flag.** "Flagged" pools the price test, no listed shares, and both. The baseline reported them apart
   once (price test 25, no borrow 20, some both); the registered sentence does not say that the report must.
3. **When it is computed.** The nightly ledger does not re-price the forward record (it runs without `--forward`:
   `deploy/fly/entrypoint.sh`), so nothing from 2026-11-23 on has been computed. That is the right state; the
   sentence does not say it must stay so until the decision.

## 3. The clarification proposed (each point for the owner to accept, change or strike)

**C1. Three labels, never two.** Every forward alert gets exactly one of: SHARES LISTED, NONE LISTED, UNKNOWN.
UNKNOWN is its own row in every table. It is never counted as unflagged.

**C2. One source for the listing, the same for taken and untaken alerts.** The runner has stored IBKR's full loan
list every 15 minutes since 2026-11-04 (`data/borrow/YYYYMMDD/HHMM.parquet`). The listing of an alert is read from
the last stored list stamped at or before the alert's posting time and no more than 20 minutes older; none such:
UNKNOWN. SHARES LISTED = that list shows at least the shares a position of the book's size needs at the alert's
price; the rule the bot's own gate uses, unchanged. For alerts the bot did stamp, its stamp is shown beside the
list's and any disagreement is counted, not resolved.
*This is an extension, not a restatement:* it defines the part the registration left open. It changes no threshold
and no registered figure. It needs the owner's word because it fixes how a registered report is computed.

**C3. Reasons kept apart.** The report shows the registered headline (any exclusion flag against none) and, beside
it, the rows: price test only, none listed only, both, neither, unknown. No row is pooled with another to make a
comparison look larger.

**C4. Two periods, never pooled.** Sessions before 2026-11-23 were read on 2026-11-21 (no flag -5.20% on 37 alerts,
flagged +6.21% on 36): development data, shown in its own column. Sessions from 2026-11-23 are unseen and are read
ONCE, at F1's decision. Nothing is computed before that; no count, no average. The nightly ledger stays without
`--forward`.

**C5. A reported split, with no verdict of its own.** It does not change F1's KEEP or DROP, it has no threshold, and
it is not a second confirmation of the cohort review: the taken shorts are inside the forward alerts, so the two
overlap and count once.

**C6. The fee is described, not banded.** For SHARES LISTED rows: the median and quartiles of the listed annual fee.
No fee band and no fee cut-off: either would be a new split with a new threshold.

**C7. What the split can say.** It speaks to ONE of four separate unknowns:

| Unknown | Does this split speak to it? |
|---|---|
| Availability: were shares listed for loan at one broker shortly before the alert | yes, and only this |
| Locate cost: what an intraday locate would have cost | no. A listed annual fee is an indicative rate, not a locate quote |
| Financing: fees and interest on a carried position | no. The rule covers inside 60 minutes; a position stuck in a halt is not priced |
| Order feasibility: the price test, displayed size, halts, the broker's approval | the price test by its own flag; the rest no |

**C8. What decision it informs.** At F1's decision: whether the verdict describes alerts with shares listed. If F1
reads KEEP and the SHARES LISTED rows from 2026-11-23 are at or under zero, the owner is told that the record's
result does not sit in alerts with shares listed, before anything else follows from the KEEP. If F1 reads DROP, the
split is recorded and changes nothing.

## 4. What this does not do

No new hypothesis, no new sample, no threshold, no early reading. H1 stays sealed and is not touched. The cohort
review and amendment F1 keep their rules and dates. No locate request, no broker account, no paid data: none is
authorised, and none is needed for this.

## 5. Effort

About a third of a working session: one function that reads the stored list at an alert's time, its tests (the
20-minute bound, UNKNOWN, a list that is missing or malformed), and the report table, built to run only when F1's
decision point is reached. One independent review before it is accepted.
