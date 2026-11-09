# Real quoted spreads vs the fade's cost model (2026-11-08)

`scripts/fade_spread_audit.py` (fetch + report; cache `reports/fade_spreads/quotes.csv`). For every tested fade it
reads the first 50 SIP quotes of the entry minute (the tested fill is that bar's open) and of entry + 60 minutes (the
timed exit), and takes the median quoted half-spread at each. A market short sells at the bid and covers at the ask,
so real round trip = entry half-spread + exit half-spread. Compared with the modelled `cost_rt` the champion
subtracts. A measurement of an assumption; nothing was selected on it.

| period | n with both legs | model round trip | real (mean / median) | net per trade, model | net, real spreads |
|---|---|---|---|---|---|
| training | 2,201 of 2,345 | 0.90% | 0.85% / 0.65% | +1.13% | **+1.18%** |
| held-out | 724 of 764 | 0.89% | 0.87% / 0.75% | +1.31% | **+1.33%** |

By entry price (training / held-out, real round trip): $1-2 0.95% / 0.91% (model 1.00%); $2-5 0.77% / 0.81% (model
1.04%); $5-10 0.75% / 0.83% (model 0.64%); $10-50 1.09% / 1.06% (model 0.64%). Net at real spreads: $1-2 +2.66% /
+3.05%, $2-5 +0.30% / -0.01%, $5-10 +0.92% / +1.21%, $10-50 +1.01% / +0.21%.

Reading: the cost model is right on average; the edge is not a cost illusion. It slightly overcharges $2-5 and
undercharges $5-50 (a few very wide quotes push the mean above the median). The money remains in $1-2.
Limits: top-of-book only (a $5,000 order on a thin name can walk the book); stopped trades exit earlier than the
60-minute quote used here; borrow / locate fees are not included (PLAYBOOK finding 9).
