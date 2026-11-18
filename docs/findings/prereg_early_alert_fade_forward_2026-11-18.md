# Pre-registration: are fades on EARLY alerts the losing third? A forward test (2026-11-18)

Status: FROZEN before any forward alert has been placed in a band and before the tagging code exists. Decided once,
on new alerts only.

## Why
`prereg_scenario_map_2026-11-18`: with RUN = the stock's rise from its low of the day to the alert (official bars
before the alert, pre-market included), the tested fade lost on the early third of alerts in both samples
(training -0.34% and -1.18% by position; held-out -0.87% and -0.19%) and earned on the rest. The cut was seen in
both samples before it was defined as a rule, so neither sample can confirm it. Owner, 2026-11-18: "survey the
rules ... account for many different scenarios." This is the first scenario switch worth a clean test.

## The rule under test (fixed here)
EARLY = RUN at the alert's start bar <= 33.6% (the training tercile cut point; it is not re-estimated). RUN is
computed as in the scenario map, from official bars before the start bar. Rule: the fade is not taken on EARLY
alerts.

## Sample
Every alert of the channel from 2026-11-04 on that the tested fade rule trades on official bars (first event per
stock and day, rule hours, $1 to $50, a start bar within 5 minutes; no-news as the live rule), scored by the
research method (the tested entry, 20% stop, 60 minutes, modelled cost and locate), whether or not the live book
took it. Each gets its band nightly from official bars (`sgt.fast.scenario`). None of these alerts was in the
scenario map (its last event is 2026-10-29).

## Decision (once)
At the first night on which 300 such alerts have a result and a band: the difference (mean net of the NOT-EARLY
alerts) minus (mean net of the EARLY alerts), session bootstrap 10,000 resamples, seed 20261118, one-sided lower
bound at alpha = 0.05. PASS = the lower bound is above 0 AND the EARLY alerts' own mean net is below 0. Before
that night only the count is read; no mean by band is looked at.

## What follows
- PASS: put to the owner as a scenario rule of the short book ("no fade on an early alert"), with the note that
  live the band would come from the free feed's candles and needs its own replay first.
- FAIL: recorded; the band stays a label on the trade cards and is not tried again as a switch.

## Known limits
About 20 qualifying alerts a day: the decision is roughly three weeks away. The live book trades only what can be
borrowed; the test is of the rule's signal on all qualifying alerts, as every earlier test of the fade.
