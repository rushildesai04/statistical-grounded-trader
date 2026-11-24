# Pre-registration: a forward-only drift monitor for the alert fade's every-alert record (2026-11-24)

Identifier: `fade-drift-monitor-v1`. Owner, 2026-11-24: "Proceed with option A as a separately registered,
forward-only drift monitor. It must not replace the existing guard, change sizing, or modify the frozen reviews."

Committed before any session it reads (first session: 2026-11-25) and before any code for it exists. A RESEARCH
monitor: it writes a line and, at most three times, a warning. It never changes a size, a gate, a position or a
flag, and nothing in it is a reason to restore or cut size.

## 0. Why, and what it does not replace
The existing drift guard (`sgt.learning.live_monitor`, playbook section 2) tests the win rate after every alert
with no allowance for repeated looks and treats alerts as independent. Its nominal p-values are unreliable in both
directions. It stays exactly as it is: its alarm of 2026-11-16 and the 25% size it set stand, and only the owner's
word changes that. This monitor runs BESIDE it, on later data only, with a design whose error rate can be stated.
It does not re-judge the past alarm and is not a restoration test.

Nothing here touches the registered review of the executable baseline (`exec_baseline_2026-11-21.md` section 6),
its sample, its counts or its criteria, nor H1 (`short-ssr-limit-v1`, sealed).

## 1. Population and metric
| | |
|---|---|
| Population | every row of the forward record `reports/fade_challengers/shadow.csv` whose `session` is 2026-11-25 or later: each channel alert of a tested kind, the first per stock and day, entry 09:30 to 15:44 ET, price $1 to $50 (the forward events of `prereg_alert_fade_2026-11-01`), scored by the frozen rule on official bars (short at the first minute after alert + 10 s, 20% stop, 60 minutes, costs and a 0.5% locate). Column `net_champion`. Rows with a non-finite value are dropped and counted |
| What it is NOT | not trades the book entered, and not executable results: the record includes news alerts, alerts the gates refuse, and shorts carrying an exclusion flag (no listed shares, price test), at the backtest's fills. A statement from this monitor is about the tested SIGNAL as the backtest defines it, never about what an account could have made |
| Reference | the held-out result of `prereg_alert_fade_2026-11-01` under the same scoring: 764 alerts, mean net **+1.12%**. Treated as a fixed number. It is itself an estimate (its own standard error is about 0.6 points); the monitor asks "is the forward record below the tested number", not "do two populations differ" |
| Excluded for good | the 113 alerts of 2026-10-30 to 2026-11-23 (seen many times, and the source of the existing alarm) and the session of 2026-11-24 (in progress when this was written) |

Metrics, in this order of weight:

1. **Primary: net expectancy.** The pooled mean of `net_champion` over the population (sum of nets / number of alerts).
2. **Second: loss severity.** The share of alerts with `net_champion` at or under -20% (in the frozen rule: a stop-out, or a loss as large). Reference: 194 stop-outs in the 764 held-out alerts = 25.4% (`prereg_fade_stop_slippage_2026-11-09`; a published count, the held-out file is not opened again). The reference counts stop-outs only, so it slightly understates the share of losses of 20% or more: the severity test is a little easier to trip than its nominal level, and is read with that in mind.
3. **Reported only: win rate** (share above zero; reference 57.3%). No test, no warning, no error budget. Also reported without a test: the mean of the worst tenth of alerts, the count of sessions and alerts, and the largest single-session share of the alerts.

## 2. Looks and horizon
The unit of time is a SESSION with at least one alert in the population (the number of alerts a day is not under
our control and varies from 1 to 11).

| Look | When | About (exchange calendar, if every session has an alert) |
|---|---|---|
| 1 | after the 20th such session is in the record | 2026-12-23 |
| 2 | after the 40th | 2027-01-25 |
| 3 (last) | after the 60th | 2027-02-23 |

- Each look reads ALL sessions from the first to that one (cumulative), exactly once, the night that session is written (21:20 ET job); the result is appended to `reports/drift_monitor/looks.jsonl` and never recomputed.
- **Minimum independent sessions: 20.** No statistic of this monitor is computed, shown or acted on before the 20th session. Between looks the nightly line shows the session count and the alert count only.
- A look also needs at least 60 alerts. If the 20th session arrives with fewer, look 1 waits for the first night with both; the later looks keep their session counts.
- **Maximum horizon: the 60th session, or 2027-03-31, whichever comes first.** If the date comes first, one final look is made on what exists, provided it has 20 sessions and 60 alerts and uses the error share of the next unused look; with less, the monitor ends "not evaluable". No extension, no fourth look.
- An unreadable or missing record on a look night postpones that look to the next night the record can be read; it is never skipped and never filled in.

## 3. Clustering: method and limits
Alerts of one day move together (one market, one tape, often one theme). Sessions are the clusters.

Method, fixed here: the ratio estimator with a cluster-robust standard error. With sessions g = 1..G, alerts n_g and
net sums S_g, N = sum n_g, mean m = sum S_g / N, and reference m0:

    r_g = S_g - m0 * n_g
    se  = sqrt( G / (G - 1) * sum r_g^2 ) / N
    t   = (m - m0) / se,   compared with Student's t with G - 1 degrees of freedom, one-sided

The severity share uses the same formulas with a 0 / 1 value per alert and m0 = 0.254, the other tail.

A session bootstrap interval (10,000 resamples of whole sessions, seed 20261124, 5th to 95th percentile) is REPORTED
beside each estimate. It decides nothing.

Limits, stated so nobody reads more into it:

- **Clustering by session does not make the inference valid with this few days.** With 20 to 60 clusters of very unequal size and heavy-tailed outcomes (single alerts of +33% and -21%), both the t reference and the bootstrap can be off, usually too narrow. The stated error rates are nominal targets under the method, not guarantees.
- Sessions are not independent of each other: a stock can run for several days, market regimes last weeks, and the channel's own selection changes. Positive correlation between sessions makes the true false-warning rate HIGHER than stated.
- One day can hold a tenth of the alerts. The largest single-session share is reported at each look; above 15% the look says so beside its result.
- The reference is treated as exact. It is not.
- Low power, by design and stated in advance: with about 8 alerts a session and a per-alert spread of about 18 points, the standard error at look 1 is roughly 1.5 to 2 points and at look 3 roughly 1 point. A warning at look 1 needs a forward mean of about -3% or worse; at look 3 about -1% or worse. **The monitor cannot tell an edge of +1.1% from an edge of zero inside its horizon.** It can only flag an expectancy that is clearly negative.

## 4. Error allocation
Total one-sided false-warning target for the whole monitor: **5%**, fixed, split before any data:

| | Look 1 | Look 2 | Look 3 | Total |
|---|---|---|---|---|
| Net expectancy below +1.12% | 0.008 | 0.012 | 0.020 | 0.040 |
| Loss severity above 25.4% | 0.002 | 0.003 | 0.005 | 0.010 |
| Critical t, expectancy (G - 1 = 19 / 39 / 59) | -2.644 | -2.349 | -2.100 | |
| Critical t, severity (other tail) | +3.273 | +2.906 | +2.662 | |

The split is additive over looks and over the two metrics (a union bound: conservative when looks overlap, as they
do). If a look is made with a different number of sessions (section 2), its critical value is the same error share
at that look's own G - 1. One book, two tested metrics: nothing else carries a test. A second book, a third metric
or a fourth look needs a dated amendment that re-splits the 5% BEFORE the data it would read, and can only make the
monitor stricter about warning.

## 5. Warning behaviour and stopping
| Event | What happens |
|---|---|
| A look where neither test passes its critical value | one line in the nightly summary's RECORD section: the look, sessions, alerts, mean net with its bootstrap interval, severity share, win rate, worst tenth, "no warning". The monitor goes on |
| A look where the EXPECTANCY test passes | WARNING: one line under NEEDS YOU in that night's summary and every night after: "research drift monitor: forward expectancy below the tested +1.12% at look k (no size change; nothing is restored or cut by this)", with the same numbers. The monitor STOPS: no later look |
| A look where only the SEVERITY test passes | the same, worded "losses of 20% or more are more frequent than tested". The monitor stops |
| Look 3 with no warning | the monitor ENDS: "no warning in 60 sessions". Reported as exactly that. It is NOT evidence that the edge exists or has recovered (section 3: it cannot see a drop to zero) |
| Horizon date first / too little data | section 2 |

- No text message of its own: everything goes in the nightly summary (at most one new line a night).
- **No control.** It writes no flag that any trading path reads. `drift_scale`, `fade_off`, the gates, the sizes and the existing guard's flags are not read for a decision and never written. A warning is information for the owner; what follows from it is the owner's decision, in words.
- **Not a restoration criterion.** No outcome of this monitor, at any look, restores size. A look with no warning is not a reason to ask.
- There is no early "all clear" and no stopping for a good result.

## 6. Overlaps and what stays separate
- The same forward record feeds the existing guard, the challenger and exit shadows and the 300-alert forward test (amendment F1 of the fade's registration). This monitor is NOT independent of any of them and is never counted as a second confirmation of one.
- It reads pooled results only: no band, kind, time-of-day or news split. The early-alert test's bands stay unread until its own count.
- Operational visibility: the nightly summary already shows the record's running mean (the existing guard's line). That line is not this monitor's, is not a look, and nothing is done on it here.
- The registered executable-baseline reviews judge the books (shorts 25 / 50 / 100, longs 15 / 30 / 60). This monitor judges the backtest-defined signal. Neither replaces the other; where they disagree the executable baseline is the one about tradable results.

## 7. Implementation, after this is merged
A module that computes exactly sections 1 to 5 from the record and the looks file, with tests for: the session and
alert minimums, one evaluation per look, the three critical values, the union of the two tests, terminal states, an
unreadable record, and a source check that the module writes no flag and is imported by no trading path. No
historical run: the code is exercised on synthetic records only, never on the 113 excluded alerts.

## Amendment A1 (2026-11-24, the same day; before any session of the population exists)
Post-merge verification on the owner's instruction: "If these checks expose a material ambiguity or mismatch,
prepare a dated amendment before examining new outcomes. Do not silently reinterpret the merged registration."
One mismatch and one ambiguity were found. Nothing of the population had been written when this was committed (its
first session is 2026-11-25). Where A1 differs from the text above, A1 holds.

**Status: registered, NOT implemented, NOT active.** No code computes it. See A1.7 for the recommendation.

### A1.1 Comparability of the reference and the monitored returns (checked, sound)
| | Reference (+1.12%, 764 held-out alerts) | Monitored |
|---|---|---|
| Events | first parsed alert per stock and day, tested kinds, entry 09:30 to 15:44 ET, price $1 to $50 | the same builder (amendment F1 of `prereg_alert_fade_2026-11-01`: "built exactly like") |
| Execution | short at the alert test's entry; first bar with high >= entry x 1.20 covers at max(entry x 1.20, that bar's open); otherwise the last close of 60 minutes; official bars | identical ("Trade, stop, window and costs: identical") |
| Costs | `cost_rt` of the gapper cost model + 0.50% locate, flat | identical |
| Return | net = (entry - cover) / entry - cost_rt - 0.50% | identical |
| Not in either | borrow availability, the price test, real quotes, real stop fills (with real stop fills the held-out mean is +0.91%, not +1.12%) | the same omissions |

Both sides are the backtest's definition, equally optimistic. The source file is fixed as
`reports/alert_fade/forward.csv` (the F1 record: columns `session`, `net`, `gross`), the file the reference test's
own code writes; `shadow.csv` holds the same events and the same net and is not read.

### A1.2 Loss severity: MISMATCH, corrected
As merged, the event was "net at or under -20%" and its reference the count of STOP-OUTS (194 of 764). Those are
not the same thing: a stop-out is defined before costs (gross at or under -20%), while a trade that was not stopped
but lost 19% before costs is under -20% after them. The reference would have understated its own event.

Corrected: **the severity event is a stop-out: `gross` at or under -20%** (in the frozen rule this is exactly "the
stop was hit", and nothing else can reach it). The reference count (194 of 764 = 25.4%,
`prereg_fade_stop_slippage_2026-11-09`) is the same event. No cost enters either side. The held-out file is not
opened. In code the comparison is `gross <= -0.20 + 1e-9`: a cover exactly at the stop is stored
as -0.19999999999999993, and an exact comparison would miss precisely the stop-outs.

### A1.3 Hypotheses, direction, weighting, small-sample correction
| | Expectancy | Severity |
|---|---|---|
| Null | forward mean net >= +1.12% | forward stop-out share <= 25.4% |
| Alternative (the warning) | mean net < +1.12% | share > 25.4% |
| Direction | one-sided, lower tail | one-sided, upper tail |
| Weighting | every alert weighs the same (the reference is a per-alert mean), so a session with eleven alerts weighs eleven times a session with one. Not session-equal | the same |
| Standard error | sessions as clusters, linearized ratio estimator, with the G / (G - 1) small-sample factor | the same |
| Reference distribution | Student's t with G - 1 degrees of freedom (not the normal) | the same |
| Not used | no further small-sample correction (no bias-reduced variance, no wild bootstrap). With 20 clusters of unequal size this is known to reject too often; section 3 says so and the allocation below is kept small at look 1 for that reason | |

### A1.4 Error allocation, in percentage points of false-warning probability
| | Look 1 | Look 2 | Look 3 | Sum |
|---|---|---|---|---|
| Expectancy | 0.8 | 1.2 | 2.0 | 4.0 |
| Severity | 0.2 | 0.3 | 0.5 | 1.0 |
| Sum | 1.0 | 1.5 | 2.5 | **5.0** |

Added, never multiplied or re-spent. A share that is not used is lost.

### A1.5 The reference's own uncertainty
The +1.12% and the 25.4% are estimates from 764 alerts (standard error of the mean about 0.6 points if alerts
were independent, more when clustered by session). They are used as FIXED thresholds chosen in advance, so the
false-warning rate concerns the statement "the forward record is below the number the test reported" and nothing
more. It is NOT a test that the edge changed: if the true historical edge was lower than +1.12%, a warning would
be more likely with no deterioration at all. A two-sample comparison would need the held-out file by session; that
file stays closed, so none is made, and no warning may be worded as "the edge has deteriorated".

### A1.6 Looks: the exact rule (replaces the last three bullets of section 2)
Let G = sessions with an alert so far, N = alerts so far, on the night a session is written.

1. Look k has a session count c_k = 20, 40, 60.
2. A look is DUE on a night when N >= 60 and at least one unmade look has c_k <= G. The look made is the unmade
   one with the LARGEST such c_k; every unmade look below it is forfeited (never made, its share lost).
3. At most one look a night; a look is made once, stored, never recomputed. Its critical value is its own share
   (A1.4) at that night's G - 1.
4. The monitor ends at the first of: a warning; look 3 made; 2027-03-31. On 2027-03-31, if a look is unmade and
   G >= 20 and N >= 60 and G is larger than at the last look made, ONE final look is made with the share of the
   lowest unmade look; otherwise it ends "not evaluable" (if no look was ever made) or with the looks it has.
5. An unreadable record postpones; it never skips and never fills in.
6. There is no other look: not on request, not after a bad week, not to "check".

So a look that lacks 60 alerts simply is not due; when the count is reached the rule above decides which look it
is. Three looks at most, in every case.

### A1.7 What it adds beyond what is already registered, and the recommendation
| Already registered | Decides | When |
|---|---|---|
| F1 of the fade's registration, on this same every-alert record | KEEP if mean net > 0 and the session-bootstrap 95% lower bound > 0; DROP if mean <= 0; otherwise on to 600 | the first month-end with 300 forward alerts (113 on 11-23; about 2026-12-31), then 600 |
| The executable baseline, shorts | pause / retire / keep on real quotes, no exclusion flag | 25, 50, 100 trades or 2027-02-26 |
| The existing guard | holds the fade at 25% until the owner's word | standing |

What this monitor would add: (a) a test on alerts none of which has been seen, where F1's sample includes the 113
already looked at many times; (b) the tested +1.12% as the bar instead of zero; (c) stop-out frequency as a tested
quantity; (d) a stated allowance for its three looks. What it would NOT add: any decision. Sizing is the owner's
word, the signal is F1's, the books are the baseline's. By its own power statement (section 3) it warns only when
the forward mean is about -3% (look 1) to -1% (look 3) or worse, and in those cases F1 fails on its own a week
later and the baseline's lines are in sight.

**Recommendation: leave it unimplemented.** It was proposed (2026-11-24, as "option A") without first checking F1,
which already is a fixed-look, pre-registered decision on this record. If the owner still wants it, A1 makes its
definitions sound and it can be built exactly as written; it needs the owner's word, and:

- **No prospective data capture is needed.** `forward.csv` is rebuilt every night from the stored alerts and the
  stored 1-minute bars for every session since 2026-10-30, so the rows from 2026-11-25 exist whether or not this
  monitor does. It depends on those alerts and bars being kept through the horizon (nothing is deleted today), and
  a rebuilt row can differ from an earlier night's if a bar is corrected: a look reads the file as it stands that
  night and stores the counts it read.
- The definitions are fixed before 2026-11-25 only if A1 is merged before that session's record is written
  (21:05 ET that day). Merged later, the population starts at the first session after the merge instead.
- With no word to build it, nothing is ever computed and the registration lapses unused on 2027-03-31.
