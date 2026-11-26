# DRAFT clarification: the forward "flagged / unflagged" split that is already registered (no new test)

**Status: DRAFT FOR THE OWNER'S REVIEW. Nothing registered, nothing computed.** Owner, 2026-11-25: "P3 must
identify overlap with the already registered borrow study and H1. Prefer a documented clarification or extension if
it asks the same question. No duplicate testing, altered thresholds, or early reading of sealed outcomes."

**Decided by the owner, 2026-11-25 evening (point C2a, the two existing "none listed" rules):** "A - report both
definitions, with the registered definition primary and the other explicitly diagnostic. Define overlap,
missingness, and denominators before evaluation. Do not select the favorable definition or duplicate an existing
sealed study."

**Confirmed by the owner, 2026-11-26:** "use the definition actually named in the relevant registration, citing its
exact source. If the forward study and executable cohort use different borrow definitions, preserve each within its
own study. Neither becomes universally authoritative. Report the alternative as diagnostic, with missing data
separate from confirmed zero listed shares." C2a and section 3a are rewritten to that. An earlier version of this
draft took the primary rule from how the forward figures had been COMPUTED and defined a position size for alerts
the bot never took; both are withdrawn (C2a says why). Still a draft, and still nothing computed: the other points
(C1 to C8) wait for acceptance.

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

**C1. One label for every alert; missing data is never a zero.** Every forward alert gets exactly one of the five
labels of section 3a: SHARES LISTED, TOO FEW, ZERO LISTED, NOT ON THE LIST, UNKNOWN. A confirmed zero (the symbol
is on the list with no shares), an absence from the list and missing data (no list could be read) are three
different rows in every table. UNKNOWN is never counted as unflagged. (The registration's own forward figures left
unstamped alerts inside the unflagged set and said so, `exec_baseline_2026-11-21.md` lines 128 to 130; taking them
out is part of this extension and is the owner's to accept.)

**C2. One source for the listing, the same for taken and untaken alerts, under the rules that already exist.** The
runner has stored IBKR's full loan list every 15 minutes since 2026-11-04 (`data/borrow/YYYYMMDD/HHMM.parquet`). The
listing of an alert is read from the last stored list stamped at or before the alert's posting time, under the SAME
age rule the bot's own stamp uses (`borrow.STALE`: a list more than 3 hours old is not used); none such: UNKNOWN. No
new age limit is introduced. For alerts the bot did stamp, its stamp is shown beside the list's and any
disagreement is counted, not resolved.
*This is an extension, not a restatement:* it defines the part the registration left open, with existing rules
only. It needs the owner's word because it fixes how a registered report is computed.

**C2a. Each study keeps the definition its registration names. Neither is the project's borrow definition.**

What the registrations say, with their sources:

| Study | Where the definition is named | Its words | How that study's registered figures were computed |
|---|---|---|---|
| Executable cohort (shorts the bot took from 2026-11-23) | `exec_model_2026-11-21.md` line 65; repeated in `exec_baseline_2026-11-21.md` lines 63 to 64 | "`no_borrow`: IBKR's list showed no shares, or fewer than the trade needed" / "flagged `no_borrow` (IBKR's list showed none or too few)" | `scripts/exec_ledger.py` lines 190 to 194: no stamp is `borrow_unknown` (not an exclusion flag); `avail <= 0 or (shares and avail < shares)` is `no_borrow`. Words and code agree |
| Forward split of amendment F1's record | `exec_baseline_2026-11-21.md` line 326: "its own rule, unchanged; reported with the flagged / unflagged split"; lines 128 to 130: "The borrow flag exists only where the bot stamped IBKR's list" | it names no borrow definition of its own: "flagged" is the same `no_borrow` flag, so the sentence above | `scripts/exec_ledger.py` lines 265 to 266: `no_borrow` only when the bot's stamp shows zero (`ib_avail <= 0`); no stamp, no flag. The clause "or fewer than the trade needed" was applied to NO forward row |

Two facts follow, stated and not chosen between:

1. **The two studies were computed under different rules** (cohort: zero or too few; forward record: zero only).
   Each stays inside its own study. Nothing here changes the cohort's rule, its sample or its review, and the roles
   below do not apply to the cohort.
2. **For the forward split the registration's words and its computation differ.** The words include "fewer than the
   trade needed". An alert the bot did not take has no trade, so for those alerts the clause has nothing to refer
   to and the two readings are the same. For the forward alerts the bot DID take a trade and its size exist
   (`scripts/exec_ledger.py` line 168), and the computed figures left the clause out.

**Roles, inside the forward split only (owner's choice A, 2026-11-25; source rule, 2026-11-26):**

| Role | Rule | Source |
|---|---|---|
| PRIMARY: the definition the registration names | flagged when zero shares are listed; or, where the alert was taken and so a trade exists, when fewer are listed than that trade needed (the size the cohort ledger already computes for it; no size is constructed for an alert that was not taken) | `exec_model_2026-11-21.md` line 65 |
| DIAGNOSTIC: the rule the registration's forward figures were computed with | flagged when zero shares are listed, and only then | `scripts/exec_ledger.py` lines 265 to 266; the figures of `exec_baseline_2026-11-21.md` section 2.2 (lines 117 to 130) |

The two can differ on one kind of alert only: taken, shares listed, fewer than the trade needed (label TOO FEW).
The diagnostic is shown beside the primary whichever looks better, is never the headline, and never replaces it.

**Neither rule is authoritative outside its study.** A sentence about borrow names the study whose rule it uses.
No document may cite one of them as "the" borrow definition, and neither study's result is restated under the
other's rule.

*Withdrawn from the earlier version of this draft:* (a) zero-only as the primary rule: that reasoned from the
computation, and the owner's rule is the definition the registration names; (b) a position size for alerts the bot
did not take, built from the book's sizing so that "too few" could be applied to them: no registration defines such
a size, so it would have been a new definition. If the owner reads line 326 differently (the split means the flags
as the forward record carried them on 2026-11-21), the two roles swap and nothing else in this text changes.

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

**Every alert of D has exactly one label, and the labels sum to D.** The label is a fact about the list; the two
definitions differ only in which labels they call flagged.

| Label | What it means | PRIMARY | DIAGNOSTIC |
|---|---|---|---|
| SHARES LISTED | the list read under C2 shows more than zero shares and, for an alert that was taken, at least the shares its trade needed | not flagged | not flagged |
| TOO FEW (taken alerts only) | more than zero listed, fewer than the trade needed | flagged | not flagged |
| ZERO LISTED | the symbol is on the list that was read, with zero shares: a confirmed zero at that broker at that time | flagged | flagged |
| NOT ON THE LIST | a list was read and the symbol is not on it. The bot's own stamp stores this as 0 (`src/sgt/fast/borrow.py` line 122: "not in IBKR's lendable list = not shortable there"), so the registered flag includes it; it is an absence from a file, not a listed zero, and is its own row | flagged | flagged |
| UNKNOWN | missing data: no list could be read under C2 | its own row, on neither side | its own row, on neither side |

The registered headline (any exclusion flag against none) is reported as the registration defines it. Beneath it,
ZERO LISTED and NOT ON THE LIST are always shown apart, so a reader can see how much of "flagged" is a confirmed
zero and how much is an absence. The bot's stamp cannot separate the two (it stores 0 for both), which is one more
reason the stored list is the single source (C2). "No shares listed at IBKR" is a statement about that broker at
that timestamp; no row is called impossible to borrow.

**Missingness.** UNKNOWN has exactly these causes, each counted on its own line: no list stored for the session;
the last list at or before the posting time is older than the existing age rule (`src/sgt/fast/borrow.py` line 27);
the stored file cannot be read; and, under PRIMARY only, a taken alert with shares listed whose trade size is not on
record. UNKNOWN is never imputed, never merged into either side, never read as zero and never dropped: its row
shows its count and its result like the others. If UNKNOWN is the largest row, the report says in its first line
that the split describes a minority of the alerts.

**Overlap between the two definitions.** They share every label and differ on one row only, TOO FEW (flagged
under PRIMARY, not under DIAGNOSTIC). That row's count is reported, and the diagnostic figures are the primary
figures with that one row moved. The two definitions are therefore never two findings: they are one split and one
named row.

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
existing age rule, each of the five labels, UNKNOWN and each of its causes, a list that is missing or malformed, the
two definitions and their one differing row), and the report table, built to run only when F1's
decision point is reached. One independent review before it is accepted.
