# Borrow-data source feasibility (2026-11-27)

Owner, 2026-11-27: "Prioritize validating the borrow-data source before approving the proposed outcome test. I
authorize a no-cost feasibility investigation using public documentation and existing authorized access. Do not
create an account, upload our alert history, accept terms, spend money, or run the return comparison yet."

**What was done, and what was not.** Read: the candidate vendor's public documentation, terms and public source
code; one page of the broker's own knowledge base (its other pages refused the reader); and our own stored copies of
the broker's public stock-loan list, 2026-11-04 to 2026-11-27, against the listener's alert rows of the same days.
From those rows only the symbol, the session, the alert time and the alert type were read. **No result of any trade
or alert was read, no account was created, nothing was uploaded, no terms were accepted, nothing was spent, and no
return comparison was run.**

## 0. The answer first

| Question | Answer | Basis |
|---|---|---|
| Whose inventory is the candidate source? | **Resolved.** QuantConnect's Interactive Brokers short data is one copy a day of Interactive Brokers' public stock-loan file, the same file this project already stores every 15 minutes | the vendor's public repository (section 1) |
| Do quantity and fee refer to the same broker, time and security? | **Yes within a day's row** (same line of the same file). Whether fees before March 2024 were backfilled is not stated | section 1 |
| When was each observation actually available? | **Not established, and not establishable from public sources.** A row carries the date its job ran; the time of day of the pull is not published, and the vendor's engine serves a date's row for that whole date | section 2 |
| Do the terms allow the processing? | Only on the vendor's platform. Export of the data "by any means" is prohibited, with no exception for small amounts; the study would need an account, the terms accepted, and the alert history processed there. Whether it is free is contradicted between the vendor's own pages | section 3 |
| Is a once-a-day list a valid stand-in for the list at the alert? | **No, on our own data.** Of alerts whose stock had shares listed the evening before, 24% were off the list entirely by the last list before the alert; of those still listed, 57% showed fewer shares and 21% showed more | section 4 |
| Is evening inventory an upper bound on intraday availability? | **No.** It was higher at the alert in 68 of 320 cases and a stock absent the evening before was listed at the alert in 11 of 81. The claim is withdrawn | section 4 |
| Is feasibility established? | **No.** The source's identity is sound and its history is real, but its timing cannot be verified, its terms need decisions that have not been made, and a daily observation does not measure what the question is about | section 6 |
| So where can the borrow question be answered? | Forward only, on the 15-minute archive this project has kept since 2026-11-04, where every alert is stamped with the list's own state and time. That is the registered split beside the forward record: about 300 alerts near early January, read once, as a direction | section 6 |

## 1. Source identity

From the vendor's public repository `QuantConnect/Lean.DataSource.InteractiveBrokersShortStock` (its sync script and
converter) and the open-source engine's `LocalDiskShortableProvider`:

- The raw input is `usa.txt` from the broker's public FTP (`ftp2.interactivebrokers.com`, user `shortstock`): the
  file `sgt.fast.borrow` already reads. One pull per processing date. The script's own comment: the broker "serves
  only the current snapshot, so a past date can only be restored from the archive".
- Each output row is date, shares available, rebate rate, fee rate, taken from the same line of that one file. So
  quantity and fee are the same broker, the same pull and the same security line.
- A value the file gives as a lower bound (`>10000000`) is stored as 10,000,000 with the sign removed.
- Tickers of other share classes than A, B, C (preferred shares, warrants and the like) are dropped.
- Rows are keyed by the ticker string of that date; the engine's reader then looks a stock up by its LATEST ticker
  (its comment: it "implicitly trusts that Symbol.Value has been mapped and updated to the latest ticker"). For a
  study joined by date and by the ticker as it was on that date, the per-date files would be the ones to use; the
  per-ticker lookup is not point-in-time across renames.
- Share counts are the raw counts of the day; nothing in the converter adjusts for splits.
- Fee and rebate parsing was added on 2024-03-12 (the repository's change history). The converter is described as
  able to reprocess the whole archive, so earlier dates may carry fees; that is not stated anywhere.
- Lost dates are documented: 2018-06-18, 2020-12-07 and **2024-01-11**, "lost without an alert" (change note of
  2026-10-28, which also made a missed pull fail loudly from then on). One is inside the training period. Whether
  other dates were lost before that fix is not stated.

What this file is, from our own copies (one snapshot of 2026-11-27, 18,791 US rows): no row has zero shares (a
stock with nothing to lend is simply absent); 748 rows sit at the 10,000,000 cap; 99.5% of counts are multiples of
100; 132 rows have shares but no published fee.

## 2. Point-in-time validity

| Item | Finding |
|---|---|
| Date on a row | the job's processing date (`date +%Y%m%d` in the sync script; the converter writes the row under that date). The broker's file carries its own timestamp line; the converter does not use it |
| Time of day of the pull | **not published.** The schedule is not in the public repository. A change note says the job assumes "the container date matches the deployment date on daily runs", which says nothing about the hour or the timezone |
| When a row is served | the engine returns the row of the algorithm's local DATE, for any time on that date. If the pull is made after the open, the row is ahead of an alert earlier that day |
| Revisions | a date is written once from that day's pull; a date can be re-run only from the vendor's archive of the same raw file. No restatement mechanism was found |
| Symbol changes and corporate actions | section 1: keyed by the ticker of the date; counts unadjusted |
| Our own archive, for comparison | each stored list carries the file's own timestamp (file names are that stamp in UTC: verified on all 602 files); lists arrive about every 15.7 minutes between about 07:50 and 16:30 ET; 20 to 36 a session |

So the one thing that decides whether a daily row can be used for an alert at 09:45 on the same date, the hour of
the pull, cannot be learned without asking the vendor. Using the previous date's row avoids look-ahead whatever the
hour, at the price measured in section 4.

## 3. Access and terms (the vendor's own pages, read 2026-11-27)

| Item | Finding |
|---|---|
| Where the data may be used | "Platform Data may be used only inside the Site's hosted environment" |
| Export | "exporting or extracting data off the platform by any means" is prohibited; "An export is not permitted because the amount is small". Retrieving platform data "or outputs containing it" through the API or an agent is named as prohibited. Own source code and backtest summaries may be exported |
| Inspection | "Company may monitor, inspect and analyze your projects, code, logs" |
| Cost | contradictory on the vendor's pages: the dataset page read earlier today shows "Free"; the licensing page says "You need an organization above the Free tier to purchase cloud access to datasets"; the pricing table shows no research environment on the free tier. Not resolved |
| Account | required for any use |
| What a study there would require | an account; acceptance of those terms; the alert history (symbols, dates, times) and the rule's replay placed on that platform; and only aggregate results brought back, if "backtest summaries" covers them, which is itself a reading of the terms and not a stated permission |

Each of those is the owner's decision and none has been made. Nothing was done toward any of them.

## 4. What our own lists show: a daily observation against the list at the alert

Population: all 552 alert rows of the listener from 2026-11-04 to 2026-11-27 midday (18 sessions), every alert
type, taken or not. "Evening" is the previous session's last list at or before 16:05 ET. "At the alert" is the last
list before the alert on the same day (median 8.5 minutes before it; none used if older than 3 hours). One broker,
one month, counts rounded to 100 and capped: a description of this sample, not an estimate for other periods.

| Evening before | At the alert | Alerts |
|---|---|---|
| shares listed | shares listed | 320 |
| shares listed | **not on the list** | 100 |
| not on the list | not on the list | 70 |
| not on the list | shares listed | 11 |
| shares listed | no list before the alert (early pre-market) | 28 |
| not on the list | no list before the alert | 3 |
| no evening list | any | 20 |

- Of the 420 alerts with shares listed the evening before and a list before the alert, **100 (24%) were off the
  list at the alert.**
- Of the 320 listed both times: fewer shares at the alert in 183 (57%), more in 68 (21%), the same in 69 (22%).
  The ratio of the two counts runs from 0.13 (10th percentile) to 1.7 (90th); in 31 cases the count at the alert
  was under a tenth of the evening's.
- In the 60 minutes after the alert the list showed fewer shares than just before it in 195 of the 320 (61%).
- The fee differed between the evening and the alert in 250 of the 320 (78%).
- Just before the alert, 176 of the 520 alerts with a list (34%) were not on it. By alert type the share not on the
  list runs from about 15% to almost 60%, on small counts in some types (new-high alerts: 55 of 111; news alerts:
  25 of 112; intraday-high alerts: 14 of 24).

What follows, and what does not:

- A list from the evening before misstates the state at the alert for roughly one alert in four on the "listed"
  side alone. A study that classifies by a daily list measures "listed the day before", not "could be borrowed at
  the alert", and any real difference between the two groups would be blurred toward nothing by that much mixing.
- Evening inventory is not an upper bound: it was exceeded at the alert in about a fifth of the cases.
- Nothing here says whether the short rule works in either group. No result was read.

## 5. Execution meaning: four things that are not each other

| Thing | What a list row says about it | Where it would have to come from |
|---|---|---|
| Listed quantity | the broker's published count at that pull, rounded, capped at 10,000,000, for its own customers | the file |
| Annual borrow fee | the published rate at that pull, when one is published. The broker's knowledge base computes the charge as loan value times rate over 360 days, on collateral marked at 102% of the prior settlement price rounded up to the next dollar | the file; the knowledge-base article read today |
| Locate charge | nothing. Whether this broker charges separately to locate, and when, was not confirmed: the broker's pricing pages refused the reader | the broker's pricing page (not read) or an account |
| Broker approval of the order | nothing. A listed share is not a locate, a locate is not an approved order, and an approved order is not a fill | an order at a broker; not observable from any list |

Also not confirmed from the broker's own text, because its pages could not be read: whether the published counts are
indicative, how often the public file is refreshed, whether intraday shorts incur the fee, and what the broker's
history tool offers. They are listed here as unconfirmed, not assumed.

## 6. Verdict, and what it changes

**Feasibility is not established, and the historical study is not prepared.**

Supporting evidence: the timing of the daily rows cannot be verified from public sources (section 2); the terms
need three decisions that have not been made (section 3); and on our own data a daily list is wrong about the
alert-time state often enough that a clean answer could not come out of it even with perfect access (section 4).

Strongest counter-argument: the vendor could answer the timing question in one message, and November 2026 may not
be typical of 2024 to mid-2026, when the alert stocks may have been easier or harder to borrow. Both are true. The
first costs an account and a conversation; the second cannot be checked without the very data in question.

What would change the verdict: the vendor stating the hour of its daily pull, together with intraday history from
some source for a sample of past alert days showing the evening-to-alert mismatch is small in the training period.
A daily source alone does not get there.

What it changes in the plan:

- The proposed development-data study is withdrawn until a source with verified timing and resolution near the
  alert exists. None is known that needs no spending or account.
- The borrow question stays with the forward records, which use the list's own state at each alert: the split
  beside the forward record (read once at the first month-end with 300 or more, near early January) and the
  executable baseline (early look at 25 shorts with no exclusion flag). Neither can be hurried; both are already
  registered.
- One fact from this work is usable now: for these stocks, borrow state moves within the day by a lot. Any future
  design that depends on borrow has to read it at the alert, not from a daily file.

Options that remain the owner's, each with what it would buy:

| Option | Buys | Costs |
|---|---|---|
| Ask the vendor the hour of its daily pull and whether fees exist before March 2024 | removes one of the three obstacles; not the resolution problem of section 4 | an account or a support contact; no money as far as known |
| An Interactive Brokers account for its stock-loan history tool | possibly history from the broker itself; what it offers is unconfirmed | an account the owner has declined before |
| Neither | the answer arrives forward, in January, as a direction on about 300 alerts | time |

Recommended: neither, for now. The no-cost route does not produce a valid answer, and the forward records do.
