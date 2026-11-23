# Strategy review: today's three losses, the selection logic, and whether a better rule is supported (2026-11-23)

Owner, 2026-11-23: "repeated explanations that 'the rule was followed' or 'this is expected variance' are not
sufficient ... determine whether better, executable decision logic can be demonstrated, and stop allocating effort
to approaches that cannot justify themselves." A bounded research track beside the frozen baseline. Paper only. The
registered cohort, its review criteria and the sealed H1 outcomes are untouched. Today's three trades are
DEVELOPMENT examples: nothing below is validated on them.

> **Corrected 2026-11-23 (afternoon, before any official result of the day existed), on the owner's review.** (1) "Placeable" is replaced by "no exclusion
> flag" throughout: no broker has approved or filled anything, so a trade without a flag is not thereby a trade that
> can be made. (2) The split of 29 pre-cohort shorts by flag is restated as what it is: a small split examined after
> the fact; a label set before the entry removes one source of hindsight and does not make it independent
> confirmation; the earlier test on 1,335 training trades that did NOT show the price-test effect is put beside it.
> (3) Each rejected candidate now says what population and execution its evidence rests on, and its conclusion is
> limited to that. (4) Section 9 separates four kinds of evidence and says plainly which cohort outcomes have been
> looked at. (5) Section 7 restates the registered review rules, unchanged.

## 0. The answer first

| Question | Answer |
|---|---|
| Why did the three trades lose? | section 2. In one line each: APLD was a rebound inside a decline bought with a target larger than the stock's whole prior half-hour range; BRAI was a short into an active breakout on a day with company news the news check did not see; BUUU was a short between trading halts that a real account could not have sold as booked. |
| Is a better rule supported? | **No, not by the evidence that exists.** Three candidate rules were checked (section 5). One finds no support in 1,238 past trades, one was already tested in related forms and lost, one is already registered and months from a decision. For the two short candidates the past data includes shorts that could not have been sold as assumed, so the checks show ABSENCE OF SUPPORT, not disproof. No variant was built: building one would have been manufacturing it. |
| What does the record suggest instead? | An observation, not a confirmed finding. Of 29 pre-cohort shorts, the 15 with no exclusion flag lost 8.9% a trade (27% winners); the 14 with a flag (no listed shares, or the price test) won 64% of the time. The same 15 lose with the backtest's own fills, so for them it is not a cost problem. Against it: the earlier test of the price-test flag on 1,335 training trades did NOT show such a split (section 3). The long idea earns about nothing before costs in every tested cell on the full tape. |
| What was fixed | The short cards said **GO** on an average that includes shorts carrying an exclusion flag. The verdict is removed for the short book and the card now says what its average is an average of (section 4). |
| Recommendation | section 7. Both alert books: finish the registered reviews, whose rules are restated there unchanged; stop developing new entry, exit and filter variants on them; keep H1 separate and sealed. |

## 1. Current state

| | |
|---|---|
| Running | runner v110 = main `d603298` (deployed Mon 01:34 ET). Merged and waiting for the after-close deploy: #204 to #208: monitoring and the short card's wording, no rule in them |
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

### BUUU short, about -$109 as booked; carries an exclusion flag (the price test)

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
  be covered while halted. In past data this kind of alert is a strong one by the backtest's fills and almost
  always carries an exclusion flag on real quotes (section 5, hypothesis A).
- **Fill assumptions realistic?** No. The booked 4.14 was a print on an exchange with under 1% of the volume while
  the tape traded 4.08 to 4.54 in that minute; the sale at the bid was not allowed at all.

**Total.** Booked estimates: about -$6, -$43, -$109, with estimated costs the dashboard's -$183. Of that, $109 is a
trade a real account could not have made as booked (a short may not be sold at the bid under the price test). The
two trades with no exclusion flag, on real quotes by a hand run of the nightly model: APLD -1.2%, BRAI -3.4% (section
9 says what kind of number that is).

## 3. Where the losses come from (today and the wider record)

Several causes can hold for one trade; none is forced into one box.

| Source | Today | The wider record |
|---|---|---|
| Wrong or late data | BUUU's entry print (free feed); BRAI's news not seen by the vendor | free feed against tape: 4 of 22 base breaks agree; one news vendor |
| Invalid setup detection | none (APLD's base exists on the tape) | 2 of 24 past longs were patterns not on the tape |
| Implementation defects | none | none open on the trading path |
| Execution assumptions and costs | BUUU (price test, 5.6% spread, halts); BRAI's 100-share bid | 14 of 29 pre-cohort shorts carry an exclusion flag (no listed shares, or the price test) |
| Entry selection | APLD (rebound, not continuation); BRAI (active breakout) | see below: this is where the loss actually is |
| Exit selection | APLD (a target 1.43 times the prior half-hour range) | every exit and target that WAS tested is worse or the same (shorts: trail, VWAP, moving averages, a fixed target; longs: 1R, 2R, 3R, part-out, 15 / 30 / 60 minutes, hold to the close). A long target tied to the recent range was never tested (section 5, C) |
| Sizing | none: $510 and $1,250 | drift guard already at a quarter size |
| No demonstrated edge | both books | the finding of this review |
| Cannot be resolved yet | the real locate charge for BRAI | locate prices; borrow before 2026-11-04 |

**One comparison here is controlled. The other is an observation.**

*Controlled (the same trades, two ways of pricing them).* The 15 pre-cohort shorts with no exclusion flag made -9.2%
a trade with the backtest's own fills and -8.9% on real quotes (27% winners, median -6.5%). For these 15 trades the
fill model is not why they lost. That is a paired comparison and it is valid; it is also 15 trades.

*Observation (two different groups of trades, compared after their results were known).* The 14 pre-cohort shorts
WITH an exclusion flag: 64% winners, median +7.3% (mean +6.3% on the backtest's fills, +5.9% on real quotes; the
mean leans on three trades of +26% to +33% and is about zero without them). Difference between the groups,
resampled by session: -16 points [-28, -5]; 7 and 8 sessions.

What that observation is, and is not:

- The flags are set from facts that existed BEFORE each entry (the prior day's drop or the day's low before the
  entry for the price test; the listed shares for borrow). So a trade cannot have earned its label by how it turned
  out. That removes ONE source of hindsight bias.
- It does not make the split independent confirmation of anything. It is 29 trades from 15 sessions of one month,
  cut after the outcomes were known, and the same 29 trades have been examined in several audits since 2026-11-21.
  It counts once, as one retrospective observation.
- **The earlier test of the same flag did NOT show this effect.** `prereg_fade_price_test_2026-11-21` (registered,
  run once, training only): 1,335 no-news fades; price-test flagged 467, +1.37% a trade [-0.26, +2.98]; unflagged
  864, **+1.76%** [+0.62, +2.93]; difference -0.38 points [-2.50, +1.70]: NOT SHOWN. In the training data the
  fades WITHOUT the flag were positive. The forward observation and the training result disagree, and nothing here
  settles which is right: four weeks may be noise or a different market; and the other flag, borrow, cannot be
  tested on past data at all (no borrow data before 2026-11-04).
- "No exclusion flag" is not "can be traded". The flags are the exclusions the model knows about. A trade without
  one can still be impossible or far more expensive than assumed (BRAI today: 100 shares on the bid against an
  order of 613, and a 665% borrow fee whose real locate cost is unknown). No broker has approved or filled any of
  these.

What this document can therefore say: there is no evidence that shorts with no exclusion flag make money; the only
forward observation of them (15 trades) is negative; and the positive training average cannot be assigned to them,
because training could not tell them apart by borrow. Whether they make money is what the registered forward
sample decides (section 7), not this split.

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
| Verdict | a broad mean that leans on five outliers and includes flagged shorts was shown as GO on an individual trade. That was wrong |

**Corrected (in this change):** the short card shows the average with NO verdict, says it is an average of every
alert "whether or not it could be shorted", and puts the result of the shorts with no exclusion flag, on real quotes, beside it. Nothing
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
| On real quotes (pre-cohort, 77 forward alerts, a frozen run) | of 12 halt-reopen alerts 9 carry an exclusion flag, 2 were not events, 1 was priced (-20.5%). Price kinds: 11 priced, about -8% ; volume kinds: 21 priced, about -1%. 11 and 21 trades cannot carry a rule against 1,238 |
| What population and execution the evidence rests on | the training check: 1,238 backtest trades at the backtest's fills (the first full-tape bar after the alert), costs modeled, borrow UNKNOWN, price-test trades included. It therefore includes shorts that could not have been sold as assumed, in unknown number. The real-quote figures: 77 pre-cohort alerts, 11 and 21 priced trades |
| Verdict | **NOT SUPPORTED; not built.** On the only large sample that exists the rule would have dropped the better kinds. That sample cannot speak for shorts with no exclusion flag, so the rule is not disproven for them; no narrower historical sample exists, and cutting the 15 forward trades by kind would be fishing. What the check does show: the strongest kind of the 2026 backtest (halt reopenings) is the one that most often carries an exclusion flag. It becomes a label in the accounting, not a filter |

### B. Treat a catalyst seen on X as company news (the BRAI case)

| | |
|---|---|
| Failure seen | the vendor's news check said NONE; 26 posts carried a press release |
| Condition at decision | the count of catalyst posts in the prior 24 hours, already recorded at every short entry |
| Prior tests | this exact question is REGISTERED as a forward test (`prereg_fade_x_2026-11-08`, amendment A2): decided once at 60 trades per group |
| Where it stands | 17 entered shorts carry the data; 6 with five or more catalyst posts. Outcomes by group have not been read |
| Evidence against | the news rule itself rests on a split of past data by the SAME vendor's news; a second source changes the rule, it does not repair it |
| What population and execution the evidence rests on | the registered test's sample is the shorts the live book enters (the borrow check applied, the price test not), judged on their official result (the backtest's fills on the full tape), not on real quotes. Even a pass would show an effect at those fills only |
| Verdict | **NOT a new experiment.** Already registered; at about one entered short a session it is months from 60 per group. It is only worth finishing if the short book survives its own review |

### C. Long only on a continuation (a break near the session high), not a rebound (the APLD case)

| | |
|---|---|
| Failure seen | a base break 6% under the session high, with a target above the prior half hour's whole range |
| Condition at decision | the break's distance from the session high; price against VWAP |
| Prior tests | `long_cases_2026-11-19`, continuation entries on the same alerts: "held up" (15 minutes after the alert still above the alert price and VWAP, within 5% of the day's high): 466 past trades, **-2.8%** a trade, 32% wins; a plain new-high entry -1.9%; against the base break's -0.9%: the more continuation, the worse. These are continuation ENTRIES, a proxy for "a base break near the high", not that exact filter. The base break already requires a close above VWAP. The base break and its neighbours on the perfect tape: every cell of base length, width, target (1R, 2R, 3R) and hold (15, 30, 60 minutes) negative in both large samples |
| Do those tests address this? | The SELECTION half, yes: same alerts, full tape, costs modeled, conditions known at the decision; the continuation version did WORSE than the base break. The TARGET half only in part: targets of 1, 2 and 3 times the risk and holds of 15 to 60 minutes were tested; a target tied to the stock's recent range (what APLD's 1.43 times points at) was NOT |
| Evidence against | the above; and the base break's exits on 775 past trades: 46% stops (-6.5%), 22% targets (+10.8%), 32% time exits (-0.3%), which nets to about zero before costs. Moving the target changes how a zero is split between more small wins and fewer large ones; three target sizes already showed no difference |
| What population and execution the evidence rests on | the same alerts as the live long book, on the full tape, with modeled costs; a long needs no borrow, so there is no availability hole here. The execution is a candle-level simulation on ideal data (the live book reads a thin feed and its setups differ from the tape's in 18 of 22 cases; stops are assumed filled at their price). So the tests show "no gross edge on ideal data"; they do not describe the live book's fills, which can only be equal or worse |
| Verdict | **Selection: NOT SUPPORTED, no new run** (another distance threshold would be the re-run with different numbers that this track forbids). **Range-tied target: untested, low priority**, recorded as the one long idea not yet run. It is not built now because the entry it would be attached to shows no gross edge to harvest; if the long book survives its early look it is the first thing to register |

### Considered and set aside without an experiment (named so they are not rediscovered)

| Idea | Why not |
|---|---|
| A spread or quote-size check at the short's entry (BRAI: 100 shares bid; BUUU: 5.6% spread) | the only real-time quote the system has is one exchange with under 1% of the volume: its spread and size are not the market's. And the 15 shorts with no exclusion flag lost with the backtest's fills too (section 3), so a fill-side check is unlikely to be what turns them. Two trades of support |
| Skip shorts when the price test is known from the prior day | that is H1's population; the baseline ledger already labels them and keeps them out of the registered sample |
| Skip shorts at an extreme borrow fee (BRAI: 665% a year) | fee data exists only since 2026-11-04 (no past sample); a trading-rule change that would need its own registration. Not examined further here: a candidate only if the short book survives its early look |
| Do not short a stock that gapped up strongly that day (a catalyst proxy without a news vendor) | not tested for the fade. Pre-market gaps by kind of news were tested as a SCANNER input (`prereg_gap_news_2026-11-18`), a different trade. Same status as the borrow fee: a rule change, registered separately if ever |

**No variant was implemented.** The requirement was one experiment "if justified"; none is. The one experiment that
addresses where the short edge is (can a short under the price test be sold with a limit at the offer, and what is
it worth) already exists: H1, registered 2026-11-22, sealed until its 30th event or 2027-02-26.

## 6. The earlier negative tests, examined rather than cited

| Test | Same strategy and population? | Full tape? | Costs and borrow? | Condition causal? | Sample | Held-out reused? | What it does and does not show |
|---|---|---|---|---|---|---|---|
| Fade exits (trail, VWAP, EMA, 15% target), 11-05 | yes: the training events | yes | costs yes; **borrow no** | yes | large | training only | every exit loses to holding 60 minutes on the backtest's population. It does NOT show that for shorts with no exclusion flag |
| Wait for a breakdown before shorting, 11-06 | yes | yes | same | yes | large | training only | entering later loses the edge. Same limit |
| Limit above the market at entry, 11-17 | yes | yes | same | yes | large | training only | every limit loses. Same limit |
| Price-test flag split, 11-21 | yes | yes | costs yes | yes | 1,335 | no | flagged +1.4%, unflagged +1.8%: the forward split is NOT in the backtest |
| Long setups and neighbours, 11-10 to 11-20 | yes | yes | costs yes; no borrow needed | yes | 775 to 2,242 | the "later" period was looked at when the entry was chosen | the long idea earns nothing before costs on perfect data. This one has no placeability hole: a long needs no borrow |

**The documented methodological defect** of every short test: no borrow data exists before 2026-11-04, so none
could be run on shorts known to have had shares available. Each of those tests therefore includes trades that could
not have been sold as assumed, in unknown number. That limits their conclusions to "on the backtest's population,
at the backtest's fills": they do not establish what an exit, a later entry or a limit does for shorts with no
exclusion flag, in either direction. The limit does not justify re-running them with new thresholds on the same
data; it can only be repaired forward, which is what the nightly ledger and its registered review do.

## 7. Recommendation, by strategy, and the registered rules restated

### The registered rules (`exec_baseline_2026-11-21.md`, section 6; `sgt.fast.exec_book`). Unchanged by anything in this document or by today's results.

| Rule | Alert shorts | Alert longs |
|---|---|---|
| Sample | trades of sessions from 2026-11-23 on that the nightly ledger labels MODELED (priced at 1 s latency, no exclusion flag), in the order made; always the FIRST N, never a later window. Flagged, no-trade and unpriceable trades are reported beside it and never counted | same |
| The one early look | the average of the first **25**: under **-5%** is a pause, put to the owner as a question | the average of the first **15**: under **-3%** is a pause, put to the owner |
| What the early look cannot do | keep a book; and no other number is acted on before the review point | same |
| Review point | the first **50** | the first **30** |
| RETIRE | average at or under **-2%** | average at or under **0** |
| KEEP (paper) | average above 0 AND lower bound above -2% | average above 0 AND lower bound above -1% |
| Lower bound | the 10th percentile of the means of 10,000 resamples of whole sessions (seed 20261123); fewer than two sessions: no bound, cannot be kept | same |
| Otherwise | INCONCLUSIVE | INCONCLUSIVE |
| The one extension | to the first **100**, or to **2027-02-26**, whichever comes first; the same three tests a last time | to the first **60**, or 2027-02-26 |
| After the extension | neither retired nor kept = NOT ESTABLISHED: the review is over, not a candidate for real money, whether paper trading goes on is the owner's choice. Fewer than 10 trades by that date: cannot be judged, NOT ESTABLISHED. No second extension | same |
| What KEEP means | the paper book goes on; never by itself a reason for real money | same |

Pre-cohort trades (the 15 and 18 quoted in this document) are NOT in these samples and are not a reason to act
before the counts are reached. H1 (`short-ssr-limit-v1`) is a separate, sealed measurement with its own rule: its
30th event or 2027-02-26; nothing in this document read it, and BUUU's outcome under H1 was not computed.

### Recommendation

| Strategy | Recommendation | The decision the remaining data resolves | When |
|---|---|---|---|
| Alert shorts (fade) | **Continue the registered measurement; pause all new development of entry, exit and filter variants.** | pause (a question to the owner) at the early look; retire, keep or inconclusive at the review point | early look at 25 trades with no exclusion flag: at the past pace of about one a session, around the end of December. Count at the time of writing: 0 |
| H1 | **Continue as registered, sealed.** It is the only test of whether a short under the price test can be sold at all | whether an independent follow-up is worth proposing | its 30th event or 2027-02-26 |
| X-catalyst test, early-alert test | leave running as registered; no effort | each decides one skip rule | 60 a group; 300 alerts. If the short book is paused at its early look, both stop mattering |
| Alert longs | **Continue the registered measurement; pause all new development.** On ideal data the idea shows no gross edge, so a full-tape live feed is not expected to rescue it | pause at the early look; retire, keep or inconclusive at the review point | early look at 15 trades: two to three weeks at one or two a session. Count at the time of writing: 0 |
| What would justify new research | a NEW kind of information, not another cut of the same alerts: actual locate prices from a broker's locate desk (access and cost to be approved first), or a different entry event than this channel's alerts | | |

Nothing here promises a profit, and nothing here says the books will fail their reviews. It says that after today's
check there is still no rule, new or old, shown to make money on shorts with no exclusion flag or on these longs,
and that the registered reviews are where that gets decided, on counts and dates fixed before the trades existed.

## 8. Independent review of this conclusion

Two reviewers with separate briefs, each asked to falsify, each able to recompute from the stored files.

| Reviewer | Grade | What it found | What was done |
|---|---|---|---|
| Statistical and causal validity | Sound | every headline number reproduced (the kind split, the 15 against 14, the -16 point difference [-28, -5]); **no selection artefact** in the split by exclusion flag (labels rest on facts from before the entry; that removes one source of hindsight and is not independent confirmation: see section 3); the comparison "same 15 trades, two fill models" is valid; no better untested hypothesis for shorts with no exclusion flag in the files. Four low notes: the +5.9% was mislabelled as backtest fills (it is real quotes; backtest fills +6.3%); the flagged group's MEAN rests on three trades; "halts are the best kind" holds in 2026 only; the continuation tests are a proxy for the exact long filter | all four applied in sections 3 and 5 |
| Trading mechanism and execution realism | Minor gaps | the reconstruction is right on every load-bearing number; GO / NO-GO is display-only in code; the selection map matches the code. Gaps: "every target was tested" overclaimed (a range-tied long target was not); the pause thresholds were stated without their registration; three simple candidates were not named (a spread or size check, the borrow fee, a gap-up proxy); the modeled BUUU cover is an ask above any printed trade; small slips (a time, an example value) | all applied: section 5 C now separates selection (rejected) from a range-tied target (untested, low priority); section 7 cites the registration; the three candidates are named and set aside with reasons |

Neither review changes the conclusion. Reviewer agreement does not establish that the books will fail, or that no
rule exists: it establishes that the files do not support one today.

## 9. Four kinds of evidence in this document, kept apart

| Kind | What it is | Where it appears here | Standing |
|---|---|---|---|
| Historical findings | registered tests on training (and, where they opened it, held-out) data, run once, before today | sections 5 and 6; the price-test result in section 3 | as written then. Several rest on a population that includes shorts that could not have been sold as assumed |
| Pre-cohort forward record | the 29 shorts and the 18 priced longs of before 2026-11-23, and the 77 alerts of one frozen run, priced by the execution model | section 3; the real-quote lines of section 5 | retrospective, small, examined repeatedly: observations, not tests |
| Today's unsettled estimates | what the paper books booked on the free feed ("est"; the dashboard) | section 2 | not official. Official results are computed tonight at 20:40 ET from the full tape |
| Corrected simulations run by hand today | the nightly model applied this afternoon to today's three trades ("modeled") | section 2; the two figures in section 0 | previews. The nightly ledger writes its own lines at 21:25 ET; where they differ, the ledger is the record |
| Registered forward outcomes | ledger lines of sessions from 2026-11-23, read at the fixed counts of section 7 | nowhere: none is evaluated here | count 0 of 25 and 0 of 15 at the time of writing |

**What has and has not been looked at in the new cohort.** The cohort is not "unread". Its first day's trades were
seen on the dashboard and in texts as they happened, and three of them were rebuilt in this document at the
owner's request, including a hand run of the model on them. That is operational visibility, and it is stated here
so nobody takes the cohort for untouched by eyes. What the registration forbids, and what was not done: changing a
rule, a size, a gate, the sample's definition or a review criterion because of those trades; choosing or tuning a
variant on them; reading the sample's average before its fixed counts. The three candidate rules of section 5 were
PROMPTED by today's trades and were checked only against earlier data; none was adopted; today's trades could not
count as validation for any of them. Earlier documents (2026-11-22) that say the cohort "has not been read" were
written before its first session and were true then.
