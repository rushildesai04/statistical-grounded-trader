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
