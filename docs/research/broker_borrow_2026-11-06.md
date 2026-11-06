# Who can actually short the fade's stocks, and at what cost? (2026-11-06)

The fade earns +1.12%/trade held-out with a 0.5% locate cost assumed; it breaks even at about 1.6% locate
(`prereg_alert_fade_2026-11-01`). Live borrow stamps show the biggest winners (CRIS, AEHL) had zero IBKR shares and
the easy-to-borrow names lost (as Alpaca-shortable names did held-out, -0.55%/trade). So the real-money question is
which broker can short these names intraday, how often, and at what locate price. Sources at the end; claims from
secondary or promotional sources are marked [VERIFY].

## The cost that matters: locate per share vs price
Locates are quoted per share, the edge is a percentage, and two thirds of the fade's trades are under $5 (median entry
$3.22; 10th-90th percentile $1.22-$10.61). The break-even locate is 1.6% of the price:

| entry price | assumed in the test (0.5%) | break-even (1.6%) |
|---|---|---|
| $1.50 | $0.007/share | $0.024/share |
| $2.50 | $0.013/share | $0.040/share |
| $4.00 | $0.020/share | $0.064/share |
| $7.00 | $0.035/share | $0.112/share |
| $15.00 | $0.075/share | $0.240/share |

A typical hot small-cap locate of a few cents per share is affordable above about $4 and ruinous on $1-2 names. Real
quotes per trade decide it; none of the brokers below publish them.

## Brokers
| broker | can short HTB small caps? | locate / borrow cost | account / rules | fit |
|---|---|---|---|---|
| Alpaca (current paper) | easy-to-borrow only | none | — | names it can short LOST in the test |
| Interactive Brokers | only names in its lendable pool (the `usa.txt` file we stamp on every alert) | no locate fee; borrow fee accrues only on SETTLED shorts, so a same-day round trip should pay none [VERIFY on a statement]; pre-borrow $20 + fee | margin; $25k for pattern day trading (US) | cheapest when shares exist; our stamps show many winners had none |
| TradeZero | yes, locate marketplace (14+ sources), locates reusable within the day | per-share fee shown per symbol, not published; credit back up to 30% on unused locates | min deposit $2,500 (dated source) [VERIFY]; PDT depends on entity [VERIFY] | most cited for HTB shorting; quotes needed |
| Cobra Trading | yes, locates | not published | ~$27k-30k minimum; $25k PDT | pro small-cap shorting desk |
| CenterPoint Securities | yes, built for low-float shorting | not published; discounts up to 30% | $50k ($30k promo) minimum | pro desk; reports of high locate fees |
| Charles Schwab | HTB shown as "Limited", needs a locate request; availability weak | higher borrow fees on HTB | margin account | not a fit for this strategy |
| Robinhood | no stock shorting | — | — | not a fit |

## What would settle it (no money needed)
1. Keep the forward record's IBKR-borrowable subset (already in the nightly digest): if those trades alone make money
   at n >= 300, IBKR works with no locate fees.
2. For the rest, real locate quotes: with a TradeZero (or Cobra/CenterPoint) login, record the quoted locate price per
   share at each alert for two weeks (paper, no trades) and compute net = gross - quote/price. That turns the
   break-even table above into a measured answer. Owner decision: opening an account is needed only for step 2.

## Sources
[IBKR: borrow fees from settlement](https://www.interactivebrokers.com/campus/traders-insight/securities/short-selling/the-risks-of-shorting-series-part-ii-borrow-fees/),
[IBKR knowledge base on short-sale borrow](https://ibkb.interactivebrokers.com/de/node/2880),
[IBKR short sale cost](https://www.interactivebrokers.com.sg/en/pricing/short-sale-cost.php),
[IBKR pre-borrow](https://www.interactivebrokers.com/campus/trading-lessons/pre-borrow-us-stocks-for-shorting/),
[EliteTrader on IB intraday shorts](https://www.elitetrader.com/et/threads/ib-auto-locate.66701/post-1029529),
[TradeZero short selling](https://tradezero.com/short-selling),
[TradeZero on hard-to-borrow stocks](https://tradezero.com/blog/hard-to-borrow-stocks-what-short-sellers-need-to-know),
[CenterPoint: easy vs hard to borrow](https://centerpointsecurities.com/easy-to-borrow-vs-hard-to-borrow-stocks/),
[Cobra Trading review](https://curvedtrading.com/articles/en/reviews/cobra-trading-review/),
[Cobra vs TradeZero](https://www.daytrading.com/cobra-trading-vs-tradezero),
[Schwab hard-to-borrow indicator](https://help.streetsmart.schwab.com/Com/3.68/Content/Hard_to_Borrow_Indicator.htm),
[Schwab: how to short sell](https://www.schwab.com/learn/story/how-to-short-sell-stocks-on-schwabcom),
[Short-sale cost overview](https://curvedtrading.com/articles/en/how/how-much-does-it-cost-to-short-a-stock/).
