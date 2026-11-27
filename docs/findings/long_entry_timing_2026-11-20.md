# Long entry timing: the buy was booked and texted a minute late (2026-11-20)

Owner, 2026-11-20, after three losing longs (SVRE, JMIA, LVWR): "there was nothing wrong with the long trades logic
today from what i saw even though they all lost but i think the main thing was being able to get in at the right time,
which is not something we are doing optimally for LONGs".

Status: exploration on the TRAINING events only (sessions before 2026-07-01). The later events were not read: no new
selection rule is proposed, and entries were already compared on both periods on 2026-11-19 (`long_cases_2026-11-19.md`),
so they would not be a clean confirmation. Script: `scripts/long_entry_timing_study.py` (result in
`reports/long_entry_timing/`). The code change below makes the live book place its tested order on time; it is not a
new rule.

## 1. Where the live book is late (facts from the runner)
The base break is decided when its candle closes: from that moment the rule's order is a market buy
(`patterns.base_break`: "buy at the next bar's open"). The live book booked it only once the feed's NEXT candle had
closed, because it read the buy price from that candle.

| | when |
|---|---|
| the break candle closes (the order is fixed) | 0 s |
| the heads-up text "LONG SETUP ARMED" (since 2026-11-18) | 1 to 16 s later |
| the trade is booked and "LONG IN" is texted, with the price of a minute earlier | 62 to 88 s later (23 of 23 live longs since 2026-11-11; median 72 s) |

Today: SVRE armed 10:03:05, booked 10:04:06; JMIA 11:55:12 and 11:56:13; LVWR 15:32:02 and 15:33:02. At the moment of
the LONG IN text 6 of the 19 earlier longs with a recorded print were already over (5 through the stop, 1 past the
target; `long_trades.text_how`). LVWR: the break candle closed at $0.9974 at 15:32:00; the tape's first price of that
minute was $1.0099; the feed's next candle opened $1.025 (the booked price); when the LONG IN text went out the tape
was near $1.05.

## 2. The same trades with the buy moved in time (training, 403 base-break trades of the live configuration)
Same stop (the base's low), same exits (stop, 2R target, 30 minutes), official tape. My replay of the stored trades
reproduces the stored official result on 403 of 403.

| the buy | trades | net per trade | before costs | wins |
|---|---|---|---|---|
| market when the break candle closes (the rule as tested) | 403 | -0.24% | +0.51% | 38% |
| market at the feed's fill candle (the stored official result) | 403 | -0.32% | +0.43% | 37% |
| one minute later (the moment of the LONG IN text) | 400 | -0.67% | +0.08% | 35% |
| two minutes later | 400 | -0.71% | +0.03% | 34% |

- **One minute late costs 0.46 points per trade** (same 400 trades, one minute late minus on time). That is the
  whole result before costs (+0.51% to +0.08%). The owner's reading is right: the moment matters more than anything
  else measured on these trades this week.
- The feed had no candle in the minute after the break on 62 of 403 trades (15%; it waited 1 to 4 minutes): that
  alone is the 0.08 points between the first two rows.
- The stored official result already assumes the on-time buy (it replays the order from the fill candle on the
  tape). So the paper record was not overstating a late fill; the text was late, and a real order sent at the LONG IN
  text would have been the third row.

## 3. Is an EARLIER buy better? No (training, every event scanned the same way; 2,450 events)
The earliest non-anticipating entry is a buy-stop resting at the base top, with no close needed.

| entry | trades | net per trade | per event |
|---|---|---|---|
| the live rule: a candle closes above the base, buy the next open | 436 | -0.21% | -0.037% |
| buy-stop at the base top | 461 | -0.48% | -0.091% |
| buy-stop one cent above the base top | 437 | -0.31% | -0.055% |

Where both trade (404) the buy-stop gets a price 0.61% lower (median) and does 0.09 points better; but it also
buys the breaks that do not hold: 57 trades the close rule never takes, -2.98% each, 7% wins (one cent above: 37
trades, -3.14%). The close above the base pays for its later price. On the confirmed breaks alone the buy-stop looks
far better (+0.32% net, 49% wins): that number is look-ahead (it knows the candle will close above) and is not a rule.

## 4. What this means
1. The rule's own moment (the close of the break candle) is the best of the moments tested: earlier without the close
   is worse, each minute later is worse.
2. The live book was not AT that moment: it booked and texted the buy 62 to 88 seconds after it. Fixed (section 5).
3. It would not have turned today's three longs: SVRE's base was not a base on the tape, JMIA's break was on the
   feed only, and LVWR bought on time at the tape's $1.0099 is still stopped at $0.9715 (-3.8% instead of -5.2%).
   What remains between the feed and the tape needs full-tape real-time data (the owner's pending broker market
   data); the candle close itself can only be cut further by streaming prints instead of closed 1-minute candles.

## 5. The change (`long_book.at_close`, live with its deploy)
A base break that is the alert's only pending long order is BOUGHT by the paper book when its candle closes (seen
within 90 seconds of the close), at that candle's close:
- one LONG IN text at the candle close (with "Break candle closed HH:MM:SS ET (Ns ago): buy at market now") replaces
  the heads-up text plus a LONG IN a minute later: one text fewer per trade;
- the paper price is the break candle's close. On the 403 training trades it is nearer the tape's price at that
  moment than the feed's next open, which the book used until now (0.43% against 0.61% off on average; more than 1%
  off on 42 against 83 trades);
- the official result is unchanged in method: the same market order replayed on the tape from the minute the
  candle closed (`long_book._score`); a buy the tape cannot fill is scored as bought at the text, as before;
- the cost cap, "no long while our short is open", the no-win check, measure-only and the last entry minute apply
  at that moment exactly as they did at the fill;
- NOT bought at the close (the old path, the feed's next candle): a break the book first sees more than 90 seconds
  late (restart, feed outage), and an alert with another long order still pending (first to fill, as tested).

**Note added 2026-11-27 (effective behaviour; this section above is the change as approved on 2026-11-20 and is left
as written).** Since `long-live-entry-v2` a live paper long also needs every closed minute since its entry to be a
candle the book holds (owner 2026-11-26 and 2026-11-27, `long_over_record_only_2026-11-25.md` section 2b). The 90 s
window was not changed. A buy at the close exists only while no later candle is held, so from 60 s after the close
the one closed minute is unobserved and the buy is not made:

| Break first seen after its candle closed | Before 2026-11-27 | Since |
|---|---|---|
| less than 60 s | bought at the close | bought at the close |
| 60 to 90 s | bought at the close | NOT bought at the close; nothing recorded; the feed's next candle decides (the old path) |
| more than 90 s | the old path | the old path |

The last 30 s of the window are therefore not operationally equivalent to the window described above. How many of
the buys at the close made so far fell between 60 and 90 s has not been measured (the one case looked at, BNAI
2026-11-25, was 13.6 s). The only measurement that bears on what such a break gives up is the training comparison
above (the same buy one minute late made 0.46 points less per trade); it was not made for this case, so it is not a
figure for it. No exemption exists and none is to be added without the owner's word.

## 6. End-to-end check (the changed book replayed on the training events, `scripts/long_bot_replay.py`'s method)
The whole changed book run alert by alert on the free feed's candles with official results, training events only
(2,464 alerts; this replay has no short book, so "no long while our short is open" is not in it):

| | before the change | after |
|---|---|---|
| base-break trades the old book made (405) | -0.18% a trade, +0.57% before costs | -0.10%, +0.65% |
| of them bought in an earlier minute / the same / a later one | | 62 / 343 / 0 |
| official price and result equal to section 2's "when the break candle closes" | | 405 of 405 |
| paper price against the official price (average distance) | 0.53% | 0.44% |
| base breaks bought that the old book never bought (the feed's next candle came too late or under the stop) | | 19, -0.22% a trade (15 bought, 4 over the cost cap) |
| base-break longs bought under the cost cap | 343, -0.44% | 357, -0.34% |
| texts per base-break trade | 3.1 (heads-up, in, out) | 2.1 (in, out) |

Every base break the old book bought is bought at the candle close by the new one: the "another order pending"
exception never applied to them. Still a losing book after costs on the training record; the change is worth about
0.1 points a trade there, and it is the difference between the second and third rows of section 2 for anyone acting
on the text.

## 7. Not done, and the forward record
- No held-out run (see Status). Not tested: a confirmation inside the minute (needs prints, not candles) and a limit
  buy on a pullback to the base top (it fills the breaks that fail and misses the ones that run: a new rule, its own
  plan if wanted).
- Forward record, no new code: every night `long_trades.entry` (the tape's price in the minute of the buy) against
  `est_entry` (the break candle's close) and `text_px` (the first print after the text) show how far the paper price
  and the text are from the tape. Reread with the first 50 base-break longs.
