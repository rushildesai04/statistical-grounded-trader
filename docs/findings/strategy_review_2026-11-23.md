# Strategy review: today's three losses, the selection logic, and whether a better rule is supported (2026-11-23)

Owner, 2026-11-23: "repeated explanations that 'the rule was followed' or 'this is expected variance' are not
sufficient ... determine whether better, executable decision logic can be demonstrated, and stop allocating effort
to approaches that cannot justify themselves." A bounded research track beside the frozen baseline. Paper only. The
registered cohort, its review criteria and the sealed H1 outcomes are untouched. Today's three trades are
DEVELOPMENT examples: nothing below is validated on them.

## 0. The answer first

| Question | Answer |
|---|---|
| Why did the three trades lose? | section 2. In one line each: APLD was a rebound inside a decline bought with a target larger than the stock's whole prior half-hour range; BRAI was a short into an active breakout on a day with company news the news check did not see; BUUU was a short between trading halts that a real account could not have sold as booked. |
| Is a better rule supported? | **No, not by the evidence that exists.** Three candidate rules were checked (section 5). One is contradicted by 1,238 past trades, one was already tested in two forms and lost, one is already registered and months from a decision. No variant was built: building one would have been manufacturing it. |
| What does the evidence say instead? | The short book's tested edge sits in trades that cannot be placed (no shares to borrow, the short-sale price test, halts). The shorts that could be placed lost 8.9% a trade (15 trades, 27% winners), and lost the same with the backtest's own fills, so it is not a cost problem; the ones that could not be placed won 64% of the time. The long book earns about nothing before costs in every tested cell on perfect data, and loses the cost. |
| What was fixed | The short cards said **GO** on an average those placeable trades do not share. The verdict is removed for the short book and the card now says what its average is an average of (section 4). |
| Recommendation | section 7. Both alert books: finish the registered early looks, which exist for exactly this and are 2 to 5 weeks away; stop developing new entry, exit and filter variants on them; keep H1, the one experiment that tests where the short edge could actually be captured. |

## 1. Current state

| | |
|---|---|
| Running | runner v110 = main `d603298` (deployed Mon 01:34 ET). Merged and waiting for the after-close deploy: #204, #205, #206 (and #207 if merged): monitoring only, no rule in them |
| Today's dashboard numbers | live ESTIMATES on the free feed's prints, booked by the paper books. Not settled: the official results are computed tonight (20:40 ET) from the full tape, and the modeled ledger (real quotes) at 21:25 ET |
| Did today's decisions use the intended data and rules? | Yes. Each decision line carries the full rule label; all three alerts arrived by push within 15 s of posting; every gate ran (APLD's short was correctly refused for company news; BRAI and BUUU passed news, beta, price and borrow checks as written) |
| A defect that invalidates strategy research? | None found. Two known DATA limits showed today and are not new: the news check uses one vendor and did not see BRAI's press release; the paper short book does not apply the short-sale price test (that is H1) |
| What the audits of the last days improved | the record (every decision with its facts), the execution measurement (the nightly ledger on real quotes), reliability (recovery, stop-check order, watchdog) |
| What they did NOT improve | the trading result. Reliability fixes are not evidence of an edge, and none is claimed |

## 2. The three trades (decision record, full tape, real quotes)

Times are ET. "Est" is what the paper book booked on the free feed; "modeled" is the same order on the full tape's
quotes one second after the decision (the nightly ledger's model, run here by hand; a model, not a fill).

### APLD long, about -$6

| | |
|---|---|
| Alert | "new high" posted 09:37:06, received 09:37:20. The SHORT was refused at 09:38: company news ("Secures $5.4 Billion Hyperscaler Lease"). The long book watched |
| Context | prior close 11.49; opened 14.11 (+23%); ran to 15.49 by 09:38; fell 9% to 14.05 by 09:53; ranged 14.05 to 14.50 for 24 minutes |
| Setup | base break: 10:17 candle closed 14.55 above the base and VWAP on the free feed (ready 10:18). On the full tape the same base breaks one minute later (10:19), same stop. Valid on the tape |
| Entry | bought 14.55 at 10:18:06 (book). Real quote: 14.53 bid / 14.54 ask, spread 0.07%. Modeled entry 14.54. Size $510 (the unproven-book size) |
| Plan | stop 14.175 (2.6% away), target 15.30 (twice the risk, +5.2%), out after 30 minutes |
| What happened | high 14.73 at 10:21 (+1.2%), then slid; under the break level by 10:34; time exit at 30 minutes (the 10:47 candle, booked 10:48) |
| Result | est 14.405, -1.0%. Modeled 14.37 at the bid, **-1.17%**. No cost component beyond the spread (0.07% in, 0.07% out) |

- **Valid base and breakout on the full tape?** Yes, one minute later, same stop.
- **Continuation or rebound?** A rebound. The break was 6.1% BELOW the session high, after a 9% fall from it.
- **Target and hold against volatility?** The target needed +5.2% in 30 minutes. The whole range of the prior half
  hour was 3.7%; the target was 1.43 times that. The 30 minutes after entry spanned 2.8%. The plan asked for a move
  the stock had not made in either half hour around it.
- **Did it deteriorate identifiably?** Yes: the tracked "trader's rule" (momentum turning down) would have sold at
  14.595 at 10:30 (+0.3%). That exit is recorded beside every long and is NOT a tested improvement (section 6).

### BRAI short, about -$43

| | |
|---|---|
| Alert | "new high" posted 10:29:33, received 0.9 s later |
| Context | prior close 1.48; a press release before the open (an acquisition plan), +17% to +24% premarket; opened 1.775 and sat at 1.77 to 1.85 for 57 minutes; then 1.86 (10:27), 1.93, 2.00 (10:29), 2.06 (10:30) on sharply rising volume (147,000 shares in the five minutes before entry, against a few thousand a minute before) |
| Checks | vendor news: NONE (the release was not in the vendor's feed; 26 posts on X carried it). Beta 0.99. Borrow: 20,000 shares listed at a 665% annual fee. Price test: not in force |
| Entry | short 2.04 at 10:30:23 (book). Real quote 2.04 bid / 2.06 ask, 1.0% spread, **100 shares on the bid against an order of 613**. Modeled entry 2.05. Size $1,250 |
| Plan | stop 2.448 (+20%), cover after 60 minutes |
| What happened | 1.945 one minute later (in favour), then a steady climb: 2.14 (10:43), 2.23 (10:50), high 2.27 at 10:53 (11% against), back to 2.06 to 2.16. A second "new high" alert at 10:50:43 (recorded as a duplicate, no second trade); the long book's own base break fired at 10:51 at 2.22 and was not bought because the short was open |
| Result | est cover 2.11, -3.4%. Modeled: 2.05 to 2.11, gross -2.9%, **net -3.4%** with the ASSUMED 0.5% locate charge. At a 665% borrow fee the real charge for a locate is unknown and could be several times that |

- **Exhausted spike or active breakout?** An active breakout: new session highs on rising volume two minutes before
  entry, after an hour of quiet, on a news day.
- **Measurable before entry without hindsight?** Yes, several ways (the alert's kind, the distance from the
  session high, the last minutes' volume). Whether any of them PREDICTS a worse fade is a different question, and
  the answer from 1,238 past trades is no (section 5, hypothesis A).
- **Invalidated before the timed exit?** By 10:50 the stock was 9% above the entry at a new high. A rule that
  covers there would have lost 9% instead of 3.4%.
- **Have invalidation rules been tested fairly?** Four were, on the training events with costs (trailing stop, VWAP,
  moving averages, a 15% target): all worse than holding 60 minutes. The limit of those tests is in section 6.

### BUUU short, about -$109 as booked; not a placeable trade

| | |
|---|---|
| What "halt resumed" meant | the stock ran 3.32 to 4.45 in 44 minutes and was halted 10:14 to 10:42. The channel posted that trading had reopened ("resumed"). That kind of alert is one of the ten the short book fades |
| Alert | posted 10:42:06, received 0.5 s later |
| Entry | the rule enters at the first usable print of the minute after the alert. The free feed had none until 10:45:08 (17 bars all day): short 4.14. On the tape that minute ran 4.08 to 4.54. Real quote at the decision: 4.16 bid / 4.40 ask, **5.6% spread**. Valid under the timing rule (inside 5 minutes; no restart involved) |
| Checks | news NONE, beta 1.74, 1.4 million shares listed at a 64% fee. **Price test: IN FORCE**, carried over from the prior day (prior low 3.00 against the close before it, 3.78): a short may not be sold at the bid |
| What happened (the part the screenshot does not show) | halted again 10:46 at 4.54; reopened 10:51, 4.10 to 4.50; halted 10:52 at 4.80; reopened 10:57, high 4.84 then 4.23; 4.10 to 4.62 until 11:10; 4.10 to 4.44 until 11:35; 4.30 to 4.65 to the exit. The 20% stop from the booked entry (4.97) was never touched |
| Result as booked | est cover 4.50 at 11:45, -8.7% |
| Result as modeled | sold at the bid one second later, 4.00; stop then at 4.80, touched at the 10:52 halt; covered 4.99, a modeled ASK after the halt, above any trade actually printed in the hour (the tape's high was 4.84): **-25%**. But the price test forbids that sale, so the ledger labels the trade UNRESOLVED and it is NOT in the registered sample |

- **Supported the fade:** +34% in 44 minutes, a halt, no news the vendor saw.
- **Contradicted it:** three halts on the way UP, a 5.6% spread, a price test in force, and a position that cannot
  be covered while halted. In past data this kind of alert is the short book's BEST by the backtest's fills and almost
  never placeable by real ones (section 5, hypothesis A).
- **Fill assumptions realistic?** No. The booked 4.14 was a print on an exchange with under 1% of the volume while
  the tape traded 4.08 to 4.54 in that minute; the sale at the bid was not allowed at all.

**Total.** Booked estimates: about -$6, -$43, -$109, with estimated costs the dashboard's -$183. Of that, $109 is a
trade a real account could not have made as booked. What a placeable version of today made: APLD -1.2%, BRAI -3.4%.

## 3. Where the losses come from (today and the wider record)

Several causes can hold for one trade; none is forced into one box.

| Source | Today | The wider record |
|---|---|---|
| Wrong or late data | BUUU's entry print (free feed); BRAI's news not seen by the vendor | free feed against tape: 4 of 22 base breaks agree; one news vendor |
| Invalid setup detection | none (APLD's base exists on the tape) | 2 of 24 past longs were patterns not on the tape |
| Implementation defects | none | none open on the trading path |
| Execution assumptions and costs | BUUU (price test, 5.6% spread, halts); BRAI's 100-share bid | 14 of 29 past shorts could not be priced as placeable |
| Entry selection | APLD (rebound, not continuation); BRAI (active breakout) | see below: this is where the loss actually is |
| Exit selection | APLD (a target 1.43 times the prior half-hour range) | every exit and target that WAS tested is worse or the same (shorts: trail, VWAP, moving averages, a fixed target; longs: 1R, 2R, 3R, part-out, 15 / 30 / 60 minutes, hold to the close). A long target tied to the recent range was never tested (section 5, C) |
| Sizing | none: $510 and $1,250 | drift guard already at a quarter size |
| No demonstrated edge | both books | the finding of this review |
| Cannot be resolved yet | the real locate charge for BRAI | locate prices; borrow before 2026-11-04 |

**The one controlled comparison that matters.** The same 15 past shorts with no exclusion flag, priced two ways:
with the backtest's own fills -9.2% a trade; on real quotes -8.9% (27% winners, median -6.5%). The fill model is not
why they lose. The 14 shorts that could NOT be priced as placeable: 64% winners, median +7.3% (mean +6.3% on the
backtest's fills, +5.9% on real quotes; the MEAN leans on three trades of +26% to +33%, without which it is about
zero, so the win rate and the median are the figures to read). Difference between the two groups, resampled by
session: -16 points [-28, -5]; 7 and 8 sessions carry it. The tested edge (+1.8% a trade over 1,238 past no-news
fades) is an average over both kinds, and the forward record splits it: positive where the trade cannot be made,
negative where it can.

Is the split an artefact? It was checked for the obvious one: a short labelled "price test" BECAUSE the stock fell
after the entry would win by construction. It is not that: every price-test label rests on the prior day's drop or
on the day's low BEFORE the entry, and the borrow label does not depend on price at all (independent review,
section 8). 15 and 14 trades are still small samples of clustered days, and this is the pre-cohort record, not the
registered sample.

For the longs: 18 past longs priced on real quotes, -3.6% a trade (22% wins); the base break replayed on 775 past
events on the perfect tape, -0.7% a trade with 0.8% of cost: about nothing before costs.

## 4. How the system selects (and what the screen claimed)

| Question | Answer |
|---|---|
| Does it rank opportunities? | **No.** It trades every alert that passes fixed checks. Nothing compares one opportunity with another |
| What makes an alert worth watching | it is one of ten kinds of channel alert on a session day. That is all |
| What makes the short book enter | the stock's first such alert that day, 09:30 to 15:44, not a "news" kind, not alerted in the prior 7 days; at entry: no company news at one vendor, beta known and under 3, price $1 to $50, enough shares listed to borrow, a usable print within 5 minutes. Fixed size, 20% stop, 60 minutes |
| What makes the long book enter | every first alert (also those the short refused) is watched for 60 minutes for a base break or a bull flag; bought at the break candle's close if the target can beat the cost, the cost is under 1%, and our own short on the stock is not open. Stop under the base, target twice the risk, 30 minutes |
| What makes it abstain | only those checks. No score, no expected value, no comparison with cost beyond the two long cost checks |
| Does GO / NO-GO influence trading? | No. It is worked out when the page is opened and is advice only |
| Are scores available at entry? | The stored "confidence" is a class index (the "no company news" class is capped at 99; 98 on today's two shorts), not a per-trade estimate; a per-trade model was tested and added no skill. It is not shown in texts since 11-10 |
| Does the scores' population match what can be traded? | **No**, and this is the finding of section 3 |

**The "GO based on 105 trades" claim.**

| | |
|---|---|
| Source | `fade_challengers/shadow.csv`: every channel alert since 2026-10-30 priced by the frozen rule the way the backtest prices it. 105 alerts through 11-20 |
| Its average | +1.05% a trade; median +0.01%; 50% wins; **-1.03% without its five best trades** (+38% to +49% each) |
| Applicability | it includes alerts with no shares to borrow and alerts under the price test. The 15 past shorts with no exclusion flag made -8.9% on real quotes |
| Freshness | through Friday; rebuilt nightly |
| Verdict | a broad mean of mostly unplaceable outliers was shown as GO on an individual trade. That was wrong |

**Corrected (in this change):** the short card shows the average with NO verdict, says it is an average of every
alert "whether or not it could be shorted", and puts the placeable shorts' result on real quotes beside it. Nothing
else uses the verdict; no trade changes. The long card already said NO-GO (-2.1% tested).

## 5. Three candidate rules, and why none is built

At most three were to be investigated. Each was held to: a mechanism, a condition known at the decision, the prior
tests that bear on it, the evidence against, and what would reject it.

### A. Do not fade price-breakout and halt-reopen alerts; fade only volume alerts

| | |
|---|---|
| Failure seen | BRAI (a "new high" alert in an active breakout) and BUUU (a "resumed" alert between halts) |
| Condition at decision | the alert's kind, fixed before looking: PRICE (new high, intraday high, testing high, gaining momentum), HALT (resumed), VOLUME (volume increasing, volume spike, gaining volume) |
| Why it might help | a volume alert may mark exhaustion, a price alert continuation |
| Prior tests | none of this split (the "where in the move" map and the wait-for-breakdown entry are different conditions) |
| Development check (one split, fixed in advance, training only: 1,238 no-news fades, 330 sessions, backtest fills) | HALT **+5.7%** a trade [+2.3, +9.1], 155 trades; PRICE +2.7% [+0.5, +4.8], 243; VOLUME +1.1% [0.0, +2.2], 795. VOLUME minus the other two: **-2.8 points** [-5.0, -0.6]. The same order by median and without each group's five best trades |
| Evidence against | the check itself: the kinds this rule would drop are never worse than the volume kind, in 2025 or in 2026. (WHICH dropped kind is best changes: price alerts in 2025, halt reopenings in 2026. "Halts are the best kind" is a 2026 statement) |
| On real quotes (pre-cohort, 77 forward alerts, a frozen run) | of 12 halt-reopen alerts 9 could not be priced as placeable, 2 were not events, 1 was priced (-20.5%). Price kinds: 11 priced, about -8% ; volume kinds: 21 priced, about -1%. 11 and 21 trades cannot carry a rule against 1,238 |
| Verdict | **REJECTED as a rule.** What it does show: the best cell of the 2026 backtest (halt reopenings) is the least placeable. It becomes a label in the accounting, not a filter |

### B. Treat a catalyst seen on X as company news (the BRAI case)

| | |
|---|---|
| Failure seen | the vendor's news check said NONE; 26 posts carried a press release |
| Condition at decision | the count of catalyst posts in the prior 24 hours, already recorded at every short entry |
| Prior tests | this exact question is REGISTERED as a forward test (`prereg_fade_x_2026-11-08`, amendment A2): decided once at 60 trades per group |
| Where it stands | 17 entered shorts carry the data; 6 with five or more catalyst posts. Outcomes by group have not been read |
| Evidence against | the news rule itself rests on a split of past data by the SAME vendor's news; a second source changes the rule, it does not repair it |
| Verdict | **NOT a new experiment.** Already registered; at about one entered short a session it is months from 60 per group. It is only worth finishing if the short book survives its own review |

### C. Long only on a continuation (a break near the session high), not a rebound (the APLD case)

| | |
|---|---|
| Failure seen | a base break 6% under the session high, with a target above the prior half hour's whole range |
| Condition at decision | the break's distance from the session high; price against VWAP |
| Prior tests | `long_cases_2026-11-19`, continuation entries on the same alerts: "held up" (15 minutes after the alert still above the alert price and VWAP, within 5% of the day's high): 466 past trades, **-2.8%** a trade, 32% wins; a plain new-high entry -1.9%; against the base break's -0.9%: the more continuation, the worse. These are continuation ENTRIES, a proxy for "a base break near the high", not that exact filter. The base break already requires a close above VWAP. The base break and its neighbours on the perfect tape: every cell of base length, width, target (1R, 2R, 3R) and hold (15, 30, 60 minutes) negative in both large samples |
| Do those tests address this? | The SELECTION half, yes: same alerts, full tape, costs modeled, conditions known at the decision; the continuation version did WORSE than the base break. The TARGET half only in part: targets of 1, 2 and 3 times the risk and holds of 15 to 60 minutes were tested; a target tied to the stock's recent range (what APLD's 1.43 times points at) was NOT |
| Evidence against | the above; and the base break's exits on 775 past trades: 46% stops (-6.5%), 22% targets (+10.8%), 32% time exits (-0.3%), which nets to about zero before costs. Moving the target changes how a zero is split between more small wins and fewer large ones; three target sizes already showed no difference |
| Verdict | **Selection: REJECTED without a new run** (another distance threshold would be the re-run with different numbers that this track forbids). **Range-tied target: untested, low priority**, recorded as the one long idea not yet run. It is not built now because the entry it would be attached to shows no gross edge to harvest; if the long book survives its early look it is the first thing to register |

### Considered and set aside without an experiment (named so they are not rediscovered)

| Idea | Why not |
|---|---|
| A spread or quote-size check at the short's entry (BRAI: 100 shares bid; BUUU: 5.6% spread) | the only real-time quote the system has is one exchange with under 1% of the volume: its spread and size are not the market's. And the placeable shorts lose with the backtest's fills too (section 3), so a fill-side check cannot be what turns them. Two trades of support |
| Skip shorts when the price test is known from the prior day | that is H1's population; the baseline ledger already labels them and keeps them out of the registered sample |
| Skip shorts at an extreme borrow fee (BRAI: 665% a year) | fee data exists only since 2026-11-04 (no past sample); a trading-rule change that would need its own registration. Not examined further here: a candidate only if the short book survives its early look |
| Do not short a stock that gapped up strongly that day (a catalyst proxy without a news vendor) | not tested for the fade. Pre-market gaps by kind of news were tested as a SCANNER input (`prereg_gap_news_2026-11-18`), a different trade. Same status as the borrow fee: a rule change, registered separately if ever |

**No variant was implemented.** The requirement was one experiment "if justified"; none is. The one experiment that
addresses where the short edge is (can a short under the price test be sold with a limit at the offer, and what is
it worth) already exists: H1, registered 2026-11-22, sealed until its 30th event or 2027-02-26.

## 6. The earlier negative tests, examined rather than cited

| Test | Same strategy and population? | Full tape? | Costs and borrow? | Condition causal? | Sample | Held-out reused? | What it does and does not show |
|---|---|---|---|---|---|---|---|
| Fade exits (trail, VWAP, EMA, 15% target), 11-05 | yes: the training events | yes | costs yes; **borrow no** | yes | large | training only | every exit loses to holding 60 minutes on the backtest's population. It does NOT show that on placeable shorts |
| Wait for a breakdown before shorting, 11-06 | yes | yes | same | yes | large | training only | entering later loses the edge. Same limit |
| Limit above the market at entry, 11-17 | yes | yes | same | yes | large | training only | every limit loses. Same limit |
| Price-test flag split, 11-21 | yes | yes | costs yes | yes | 1,335 | no | flagged +1.4%, unflagged +1.8%: the forward split is NOT in the backtest |
| Long setups and neighbours, 11-10 to 11-20 | yes | yes | costs yes; no borrow needed | yes | 775 to 2,242 | the "later" period was looked at when the entry was chosen | the long idea earns nothing before costs on perfect data. This one has no placeability hole: a long needs no borrow |

**The documented methodological defect** of every short test: no borrow data exists before 2026-11-04, so none
could be run on the trades that can actually be placed. That is a real limit, and it cuts one way only: it means
the short tests may be WRONG about placeable shorts in either direction. It does not justify re-running them with
new thresholds on the same unplaceable-inclusive data; it can only be repaired forward, which is what the nightly
ledger and its registered review do.

## 7. Recommendation, by strategy

| Strategy | Recommendation | The decision the remaining data resolves | Stopping condition |
|---|---|---|---|
| Alert shorts (fade) | **Continue the registered measurement; pause all new development of entry, exit and filter variants.** | pause or keep, at the registered early look | as registered (`exec_baseline_2026-11-21.md`, "One early look per book"; `exec_book.EARLY`), unchanged here: the first 25 forward shorts with no exclusion flag; under -5% on real quotes is a pause put to the owner. Past pace about one a session: around the end of December. Prior from 15 pre-cohort trades: -8.9% |
| H1 (price-test shorts with a limit at the offer) | **Continue as registered.** It is the only test of whether the edge can be reached where it lives | whether an independent follow-up is worth proposing | its 30th event, or 2027-02-26. No peeking |
| X-catalyst test, early-alert test | leave running as registered; no effort | each decides one skip rule | 60 per group; 300 alerts. If the short book is paused at its early look, both stop mattering and are closed |
| Alert longs | **Continue the registered measurement; pause all new development.** The idea shows no gross edge on perfect data, so better data (a full-tape live feed) is NOT expected to rescue it | pause or keep | as registered, same place, unchanged: the first 15 forward longs; under -3% is a pause put to the owner. Two to three weeks at one or two longs a session. Prior from 18 pre-cohort trades: -3.6%. (The 300-trade proof in `long_book.proven` is a different thing: it governs the book's SIZE, not this review) |
| What would justify new research | a NEW kind of information, not another cut of the same alerts: actual locate prices for the unborrowable names (needs a broker's locate desk: access and cost to be approved first), or a different entry event than this channel's alerts | | |

Nothing here promises a profit, and nothing here says the books will fail their reviews. It says that after today's
check there is still no rule, new or old, shown to make money on trades that can be placed, and that the registered
reviews are the place where that gets decided, on dates, with criteria fixed before the trades existed.

## 8. Independent review of this conclusion

Two reviewers with separate briefs, each asked to falsify, each able to recompute from the stored files.

| Reviewer | Grade | What it found | What was done |
|---|---|---|---|
| Statistical and causal validity | Sound | every headline number reproduced (the kind split, the 15 against 14, the -16 point difference [-28, -5]); **no selection artefact** in the placeable / unplaceable split (labels rest on facts from before the entry); the comparison "same 15 trades, two fill models" is valid; no better untested placeable hypothesis in the files. Four low notes: the +5.9% was mislabelled as backtest fills (it is real quotes; backtest fills +6.3%); the unplaceable MEAN rests on three trades; "halts are the best kind" holds in 2026 only; the continuation tests are a proxy for the exact long filter | all four applied in sections 3 and 5 |
| Trading mechanism and execution realism | Minor gaps | the reconstruction is right on every load-bearing number; GO / NO-GO is display-only in code; the selection map matches the code. Gaps: "every target was tested" overclaimed (a range-tied long target was not); the pause thresholds were stated without their registration; three simple candidates were not named (a spread or size check, the borrow fee, a gap-up proxy); the modeled BUUU cover is an ask above any printed trade; small slips (a time, an example value) | all applied: section 5 C now separates selection (rejected) from a range-tied target (untested, low priority); section 7 cites the registration; the three candidates are named and set aside with reasons |

Neither review changes the conclusion. Reviewer agreement does not establish that the books will fail, or that no
rule exists: it establishes that the files do not support one today.
