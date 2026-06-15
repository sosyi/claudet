# Binance ↔ Gate.io Spot Arbitrage Scanner

A dependency-free Python tool that compares **spot** top-of-book prices on
[Binance](https://www.binance.com) and [Gate.io](https://www.gate.io), finds
markets where the same asset trades at different prices, and reports the
**net spread after trading fees** so you can see real arbitrage room — not just
a raw price gap.

It works for *any* market listed on both venues: major coins, alts, meme tokens,
and tokenized stocks (e.g. `SPCX/USDT`). Whatever overlaps, it compares.

> **What "arbitrage" means here.** The realistic cross-exchange play is
> *inventory-balanced*: you hold both the asset and USDT on **both** exchanges,
> so you can buy on the cheaper venue and sell on the dearer one at the same
> instant — no on-chain transfer in the moment. The tool finds these
> simultaneous buy/sell spreads. Withdrawal/transfer cost only matters when you
> later rebalance, so it is *not* baked into every number.

## Quick start

No third-party packages are needed — just Python 3.9+.

```bash
# See the output format immediately, no network required (bundled sample data):
python -m arb --demo

# Scan every USDT market live, show the 25 widest net spreads:
python -m arb --quote USDT --top 25

# Only opportunities that clear 0.5% net after fees, on liquid markets:
python -m arb --min-net-spread 0.5 --min-volume 1000000

# Live dashboard, refresh every 10 seconds (Ctrl-C to stop):
python -m arb --watch 10 --min-net-spread 0.3

# Depth-aware profit analysis + markdown report (how much can you ACTUALLY make):
python -m arb --analyze --capital 10000 --report REPORT.md
python -m arb --demo --analyze --min-net-spread -5    # offline preview
```

## Depth-aware profit analysis (the real "profit space")

Top-of-book spread only tells you a gap *exists*. The money question is **how
much capital you can deploy before slippage closes the gap**. With `--analyze`
the tool fetches L2 order-book depth for the top candidates and simulates
walking both books at once — buying into progressively pricier asks while
selling into progressively cheaper bids — until the marginal unit no longer
clears fees. It reports, per market:

- **预算内净利润 / 净%** — realized net profit at your `--capital`, after slippage and both-leg fees.
- **可吃下资金** — the maximum capital the gap can absorb before it stops paying.
- **资金上限净利润** — the depth-limited best-case profit for one execution.
- **状态** — `受资金限制` (more capital would earn more), `受深度限制` (gap exhausted), or `扣费后不盈利` (a phantom gap fees eat).

This is the difference between a price gap that *looks* tradable and one that
*is*. See [`REPORT.md`](./REPORT.md) for a full generated example.

Analysis options: `--capital N` (budget per trade), `--analyze-top N` (how many
candidates to deep-analyze), `--depth-limit N` (book levels to fetch),
`--cycles-per-day N` (for the daily-profit sketch), `--report FILE`.

### Example output (`--demo`)

```
# 2026-06-13 03:59:47  common markets: 7  reported: 2  (fees binance=0.100% gate=0.200%)
SYMBOL     BUY @       PRICE  SELL @       PRICE  GROSS%    NET%  MIN VOL(quote)
---------  -------  --------  -------  ---------  ------  ------  --------------
PEPE/USDT  binance  1.22e-05  gate     1.235e-05  +1.230  +0.926       6,200,000
SPCX/USDT  gate       161.34  binance      163.3  +1.215  +0.912         540,000
```

The `SPCX` row is exactly the scenario in the original request: Binance ~163.37,
Gate ~161.27 → **buy on Gate, sell on Binance**, +1.21% gross / +0.91% net.

## How it reads each column

| Column | Meaning |
|---|---|
| `BUY @` / `PRICE` | Exchange you buy on (its **ask**) and the price you pay |
| `SELL @` / `PRICE` | Exchange you sell on (its **bid**) and the price you receive |
| `GROSS%` | Spread before fees: `(sell_bid − buy_ask) / buy_ask` |
| `NET%` | Spread after taker fees on **both** legs — the number that matters |
| `MIN VOL(quote)` | 24h quote-currency volume of the **thinner** of the two markets (liquidity) |

Both directions are evaluated for every shared market; the more profitable one
is reported.

## Options

```
--quote SYM            Only compare markets with this quote currency (e.g. USDT)
--min-net-spread PCT   Minimum after-fee spread to report (default 0.1)
--min-volume N         Minimum 24h quote volume on the thinner market
--top N                Show at most N rows (default 25)
--binance-fee F        Binance taker fee fraction (default 0.001 = 0.10%)
--gate-fee F           Gate taker fee fraction   (default 0.002 = 0.20%)
--format {table,json,csv}
--watch SECONDS        Refresh continuously instead of running once
--timeout SECONDS      Per-request HTTP timeout (default 15)
--demo                 Use bundled sample data instead of the network
```

Set fees to your *actual* tier (e.g. `--gate-fee 0.0015` with a GT discount,
or `--binance-fee 0.00075` with BNB) — fee assumptions change which rows clear.

## Architecture

```
arb/
  exchanges.py   # Public-REST clients for Binance & Gate, normalized to (BASE, QUOTE) keys
  core.py        # Fee model + top-of-book opportunity finder (both directions)
  depth.py       # L2 order-book fetch + book-matching execution simulation (slippage)
  analyze.py     # Depth-aware profit analysis: realized profit, max usable capital
  report.py      # Renders the markdown profitability report
  cli.py         # argparse CLI: scan, --watch loop, --analyze/--report, --demo
  data/sample_tickers.json      # Offline ticker fixture (--demo scan)
  data/sample_orderbooks.json   # Offline L2 depth fixture (--demo analyze)
tests/test_core.py    # Matching & spread math
tests/test_depth.py   # Book-matching simulation & profit analysis
```

Two-stage analysis keeps it efficient: a cheap top-of-book scan of *all* markets
picks candidates, then depth is fetched only for the top N worth deep-analyzing.

- **Symbol matching** normalizes Binance `BTCUSDT` (split via `exchangeInfo`)
  and Gate `BTC_USDT` to a common `(BASE, QUOTE)` key, so only genuinely
  identical markets are compared.
- **Read-only & key-less:** only public price endpoints are called. The tool
  *detects* opportunities; it does **not** place orders.

## Running the tests

```bash
python -m pytest -q          # if pytest is installed
# or, with no dependencies:
python -c "import tests.test_core as t; [getattr(t,n)() for n in dir(t) if n.startswith('test_')]; print('ok')"
```

## Network access note

The scanner calls `https://api.binance.com` and `https://api.gateio.ws`. In a
sandboxed/CI environment these hosts may be blocked by an egress allowlist; add
both to the allowlist, or use `--demo` to exercise the tool offline. The CLI
prints this hint automatically if a request is refused.

## ⚠️ Practical caveats (read before trading real money)

- **Spreads are fleeting.** Quotes move in milliseconds; a gap you see may be
  gone before you act. Treat output as a *signal*, not a guarantee.
- **Top-of-book only.** `bid`/`ask` reflect the best price for a *small* size.
  Large orders walk the book and get worse fills — model depth before sizing up.
- **Fees & withdrawal limits.** Default fees are conservative; set yours.
  Rebalancing inventory across exchanges incurs withdrawal fees and network time.
- **Listing/transfer frictions.** Tokenized stocks and some tokens can't be
  freely withdrawn between venues, and may halt around market events.
- This is an analysis tool for research/education. Trade at your own risk.
```
