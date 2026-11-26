# DRAFT clarification: the forward "flagged / unflagged" split that is already registered (no new test)

**Status: DRAFT FOR THE OWNER'S REVIEW. Nothing registered, nothing computed.** Owner, 2026-11-25: "P3 must
identify overlap with the already registered borrow study and H1. Prefer a documented clarification or extension if
it asks the same question. No duplicate testing, altered thresholds, or early reading of sealed outcomes."

**Decided by the owner, 2026-11-25 evening (point C2a, the two existing "none listed" rules):** "A - report both
definitions, with the registered definition primary and the other explicitly diagnostic. Define overlap,
missingness, and denominators before evaluation. Do not select the favorable definition or duplicate an existing
sealed study." C2a is rewritten to that and section 3a fixes the three definitions. Still a draft, and still
nothing computed: the other points (C1 to C8) wait for acceptance.

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

**C2. One source for the listing, the same for taken and untaken alerts, under the rules that already exist.** The
runner has stored IBKR's full loan list every 15 minutes since 2026-11-04 (`data/borrow/YYYYMMDD/HHMM.parquet`). The
listing of an alert is read from the last stored list stamped at or before the alert's posting time, under the SAME
age rule the bot's own stamp uses (`borrow.STALE`: a list more than 3 hours old is not used); none such: UNKNOWN. No
new age limit is introduced. For alerts the bot did stamp, its stamp is shown beside the list's and any
disagreement is counted, not resolved.
*This is an extension, not a restatement:* it defines the part the registration left open, with existing rules
only. It needs the owner's word because it fixes how a registered report is computed.

**C2a. An inconsistency that exists today, for the owner to settle rather than for this draft to choose.** The
ledger defines "none listed" in two ways:

| Rows | Rule in `scripts/exec_ledger.py` | A missing stamp |
|---|---|---|
| shorts the bot took (the cohort) | `no_borrow` when the list shows no shares OR fewer than the position needs | `borrow_unknown`, which is NOT an exclusion flag: the trade is labelled MODELED |
| forward alerts | `no_borrow` only when the list shows no shares at all | no flag |

**Decided: both are reported, in fixed roles.**

| Role | Rule | Why it has that role |
|---|---|---|
| PRIMARY | none listed = the list shows no shares at all (the forward rows' existing rule) | it is the rule the registered split was computed with: the baseline's own flagged / unflagged figures for the forward record (2026-11-21) came from `exec_ledger.forward_rows` under this rule |
| DIAGNOSTIC | none listed = no shares, or fewer than the position needs (the cohort's existing rule, applied to forward alerts) | an existing rule of the same ledger, shown so that the reader sees what the stricter reading would say. It is never the headline and never replaces the primary, whichever looks better |

*My reading of "the registered definition", to be confirmed:* the split is registered for the forward record, so
its registered rule is the forward rows' one. If the owner meant the cohort's rule, the two roles swap and nothing
else in this text changes.

Whether `borrow_unknown` should stay inside the cohort's MODELED sample is a question about a registered sample and
is NOT proposed for change here; it is stated so that "no exclusion flag" is read for what it is.

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

## 3a. Fixed before any evaluation: denominators, missingness, overlap

**Denominator.** D = every forward alert the forward record scores (status ok: the same rows amendment F1
averages), sessions from 2026-11-23 to F1's decision session, each alert once. Alerts of those sessions the record
cannot score are counted beside D with their reason and are in no row. Sessions before 2026-11-23 form their own
table with their own denominator and are never added to D (C4).

**Under each definition every alert of D has exactly one label, and the labels sum to D.**

| Label | PRIMARY | DIAGNOSTIC |
|---|---|---|
| SHARES LISTED | the list read under C2 shows more than zero shares | it shows at least the shares the position needs |
| NONE LISTED | it shows zero, or the symbol is not on a list that was read | it shows fewer than the position needs (zero and absent included) |
| UNKNOWN | no list could be read under C2 | no list could be read, or the position's shares cannot be computed |

Shares the position needs, for an alert the bot did not take: the dollars the book's own sizing gave a short that
session (the same function, read-only, with the drift scale recorded for that session), divided by the forward
record's entry price. No new sizing is defined. A symbol absent from a list that was read is "none listed", as the
bot's own stamp treats it (`borrow.lookup`: "not in IBKR's lendable list = not shortable there", `ib_avail` 0).

**Missingness.** UNKNOWN has exactly these causes, each counted on its own line: no list stored for the session;
the last list at or before the posting time is older than the existing age rule; the stored file cannot be read; and
(diagnostic only) no position size. UNKNOWN is never imputed, never merged into either side, and never dropped: its
row shows its count and its result like the others. If UNKNOWN is the largest row, the report says in its first
line that the split describes a minority of the alerts.

**Overlap between the two definitions.** One 3 x 3 table of counts, PRIMARY label by DIAGNOSTIC label. By
construction every PRIMARY NONE LISTED alert is DIAGNOSTIC NONE LISTED, so the two can differ in one cell only:
listed, but fewer shares than the position. That cell's count is reported, and its result is reported as
diagnostic. The two definitions are therefore never two findings: they are one split and one named cell.

**Overlap with other registered work.** The taken shorts of the cohort are inside D and count once (C5). H1's
events are inside D as alerts; H1's own figures are not read, computed or reproduced. The after-day borrow test
(`prereg_after_day_borrow_forward_2026-11-17`) uses the same alerts for a different trade; its sealed results are
not read, and no close-to-close figure is computed here.

**No selection.** The report's wording, row order and headline are fixed by this section before any number
exists: headline = PRIMARY; DIAGNOSTIC and the differing cell follow, labelled diagnostic. Neither is dropped,
re-ordered or re-labelled after the numbers are seen.

## 4. What this does not do

No new hypothesis, no new sample, no threshold, no early reading. H1 stays sealed and is not touched. The cohort
review and amendment F1 keep their rules and dates. No locate request, no broker account, no paid data: none is
authorised, and none is needed for this.

## 5. Effort

About a third of a working session: one function that reads the stored list at an alert's time, its tests (the
existing age rule, UNKNOWN and each of its causes, a list that is missing or malformed, the two definitions and their
one differing cell), and the report table, built to run only when F1's
decision point is reached. One independent review before it is accepted.
