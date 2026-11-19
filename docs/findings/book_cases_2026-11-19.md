# The other books by case: what the long book's exploration says about each (exploration, 2026-11-19)

Owner, 2026-11-19, after the long book's entry was changed (`long_cases_2026-11-19.md`): "extrapolate this same
analysis you just did for LONG with all the other books so we can improve them as much as possible."

**What this is.** The same two questions put to every other book: (1) what did every earlier test of this book take
for granted, and (2) the book's trades by the situation at the decision, and under actions that break that
assumption. An exploration: stored events, stored predictions and stored prices; both periods were read where a
later period exists; several cuts were looked at. Nothing here is a finding. A lead is worth a forward record or a
pre-registered test, never a rule by itself. Sealed samples stayed sealed (the scanner's 2026 events; the forward
record of the early-alert test by band).

## 1. Alert fade (the short at the alert)

**What every test took for granted:** short at the first minute after the alert. For the long book that timing was
the flaw. For the short it is not.

| On the same alerts (after costs and locate) | Training (2,337) | Later period (764) |
|---|---|---|
| the rule: short at the alert, 20% stop, 60 minutes | +0.95% · 55% wins | +1.12% · 57% wins |
| the same, but cover when the stock bases and breaks UP (the long book's signal) | no-news +1.89% vs +1.84% held | no-news +1.66% vs +1.97% held |
| do not short at the alert; short a base BREAKDOWN (mirror of the long entry), 2R / 30 min | n 1,001 · **-1.86%** | n 334 · **-1.32%** |
| those same alerts under the rule | +7.29% · 77% wins | +7.14% · 79% wins |

Waiting for the stock to "prove itself" works for the long and destroys the short: by the time a base breaks down the
fall the rule was paid for is over (playbook 3.3). Covering at the long book's signal changes nothing: alerts where
the stock later bases and breaks up are the losing fades (-2.9% / -1.7%) and the others the winning ones (+3.3% /
+2.6%), but that is known only afterwards. This simulation reproduces the stored result of all 3,101 fades exactly.

**The lever for the short is which alerts, not how.** By the situation at the alert (no-news alerts):

| Situation | Training | Later period |
|---|---|---|
| stock up under 20% from its low of the day | n 161 · -1.98% | n 60 · -2.12% |
| up 20 to 50% | n 580 · +0.27% | n 274 · +1.10% |
| up 50 to 100% | n 361 · +5.18% | n 110 · +4.80% |
| up 100% or more | n 133 · +3.86% | n 43 · +5.97% |
| last 5 minutes: under +5% | n 340 · -0.67% | n 164 · +0.08% |
| last 5 minutes: +15% or more | n 394 · +4.52% | n 128 · +4.97% |

The fade is paid on big, fast spikes and loses on small ones. This is the same thing the scenario map of 2026-11-18
found, and it is already in a clean forward test (`prereg_early_alert_fade_forward_2026-11-18`, decided at 300 new
alerts; its forward results by band were not read here). Nothing is changed before that test decides.

By kind of company news (all alerts): offering -2.34% / -4.48% (n 84 / 14), the worst kind; the news gate already
stops these. No kind of news is a reliable fade in both periods, so nothing argues for lifting the gate by kind.

**For real money, one thing to know:** on 36% of fades the long book's base break fires while the short is still
open. Both paper books then hold opposite sides of one stock and pay two round trips for a flat position. The books
stay separate on paper (each is measured cleanly); a real account would need one position per stock.

## 2. The daily model's long calls (`megacap_long`, paper)

**What every test took for granted:** every long call is the same trade. It is not.

| Long calls, next close-to-close move (training 2020 to 2026-06) | The 8 names first looked at | The other 13 names |
|---|---|---|
| all long calls | n 1,285 · +0.37% | n 2,163 · +0.31% |
| long call on a day the stock is DOWN 1% or more | n 597 · **+0.70%** | n 898 · **+0.58%** |
| long call on any other day | n 688 · +0.18% | n 1,265 · +0.11% |
| an ordinary day of these stocks (no model) | +0.16% | +0.11% |
| ANY day the stock is down 1% or more (no model) | n 3,196 · +0.25% | n 5,019 · +0.33% |
| a down day WITHOUT a long call | n 2,599 · +0.15% | n 4,121 · +0.27% |

Read: more than half of the long calls (on days the stock is not down) earn an ordinary day: no edge. All of the
edge is in the calls made on a down day, and there the model does add something over "just buy the dip" (+0.70% /
+0.58% against +0.25% / +0.33%). The cut was seen on 8 names and then held on the 13 not looked at. Later period
(2026-07 to 2026-10): 42 and 91 long calls, too few to read (down-day calls +0.57% vs -0.24% on the 8; +0.28% vs
+0.60% on the 13).

Other cuts, training, the 8 names: holding a long call longer earns +0.65% over 2 days, +0.78% over 3, +1.03% over 5,
but against the stocks' ordinary +0.15% a day the extra is in the first two days only; where the next day's move
comes from depends on the day (after a down day the bounce comes in the next session, +0.35% to +0.63%, not
overnight; after an up day the stock is up overnight and gives it back in the session). Short calls: the stock
moved -0.03% after them: no edge, as known.

`megacap_long` is a registered forward test (`prereg_megacap_long_forward_2026-11-13`): its rule is not changed. The
lead ("long calls on down days only") can be recorded beside it as a second ledger from the same calls, with its own
fixed decision. Put to the owner.

Limits: the 80 never-seen large caps (`prereg_fresh_names_2026-11-17`, FAIL for long calls as a whole) have no stored
rows by day, so the cut could not be checked there; six situation variables were looked at and this is the one that
matches what is already known of the model (it buys dips: `prereg_dip_timer_2026-11-17`).

## 3. The scanner (our own spikes; paused)

**What every test took for granted:** a spike is a spike. Section 1 says the channel's fades are paid on big, fast,
no-news spikes. If that were what the channel "sees", our own detector could find them without the channel.

Stored training spikes of `prereg_self_fade_2026-11-06` (2024-10 to 2025-12; the 2026 events stay sealed):

| Our own spikes, short as the fade | All | No company news |
|---|---|---|
| the tested detector (5% in 5 min, 5x volume, new high) | n 517 · -1.23% | n 358 · -1.47% |
| up 50 to 100% from the day's low | n 85 · -0.20% | n 58 · +1.05% |
| up 100% or more | n 17 · -8.81% | n 9 · -16.03% |
| last 5 minutes +15% or more | n 63 · -0.86% | n 46 · +0.06% |
| up 50%+ AND last 5 minutes +15%+ | n 36 · -3.85% | n 25 · -1.64% |

No. What sorts the channel's alerts does not sort our own spikes: no cell is positive with a usable count, the
largest runs are the worst, and the one positive cell flips between the two halves of the period (any-variant
events: -1.98% then +1.23%). Playbook 3.11 stands: the fade's edge is the channel's selection. The scanner stays
paused; this file gives no reason to un-pause it or to open the sealed sample.

## 4. ETF yardsticks
Monthly equal-weight baskets held as a measuring stick for the other books. There is no entry, exit or case to tune;
changing them would remove the yardstick.

## 5. What follows
| Book | Change | Why |
|---|---|---|
| alert fade | none | its entry is right; the "which alerts" lead is already in a clean forward test |
| daily model long calls | owner's choice: a second ledger "down-day long calls only" beside the registered book | all of the tested edge is in those calls, on both sets of names (8 and 13) |
| scanner | none, stays paused | the channel's situation does not transfer to our own spikes |
| ETF yardsticks | none | a yardstick |
| real money (later) | one position per stock across the short and long books | 36% of fades overlap a long base break |

Scratch code and tables of this exploration are not in the repo.
