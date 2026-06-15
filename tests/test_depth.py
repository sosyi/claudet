"""Tests for depth-aware execution simulation and profit analysis."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from arb.analyze import analyze_opportunity
from arb.core import Fees, find_opportunities
from arb.depth import OrderBook, load_books_from_fixture, match_books
from arb.exchanges import Ticker, load_from_fixture

HERE = os.path.dirname(__file__)
TICKERS = os.path.join(HERE, "..", "arb", "data", "sample_tickers.json")
BOOKS = os.path.join(HERE, "..", "arb", "data", "sample_orderbooks.json")


def test_match_books_stops_when_unprofitable():
    # Buy asks rise, sell bids fall; only the first pairing clears fees.
    asks = [(100.0, 10), (101.0, 10)]
    bids = [(100.5, 10), (99.0, 10)]
    fill = match_books(asks, bids, buy_fee=0.0, sell_fee=0.0)
    assert fill.base_volume == 10           # only first level pairing
    assert fill.profit > 0
    assert fill.levels_used == 1


def test_match_books_respects_capital_cap():
    asks = [(100.0, 100)]
    bids = [(110.0, 100)]
    fill = match_books(asks, bids, buy_fee=0.0, sell_fee=0.0, max_quote=1000.0)
    assert fill.capital_capped is True
    assert fill.quote_spent <= 1000.0 + 1e-9
    assert abs(fill.base_volume - 10.0) < 1e-9   # 1000 / 100


def test_match_books_fees_can_eliminate_profit():
    # 0.1% gap is wiped out by 0.1% + 0.2% fees.
    asks = [(100.0, 10)]
    bids = [(100.1, 10)]
    fill = match_books(asks, bids, buy_fee=0.001, sell_fee=0.002)
    assert fill.base_volume == 0.0
    assert fill.profit == 0.0


def test_analyze_opportunity_budget_vs_depth():
    books = load_books_from_fixture(BOOKS)
    tickers = load_from_fixture(TICKERS)
    opps = find_opportunities(tickers["binance"], tickers["gate"], "binance", "gate",
                              Fees(), min_net_spread_pct=-100)
    spcx = next(o for o in opps if o.base == "SPCX")
    buy_book = books[spcx.buy_exchange][(spcx.base, spcx.quote)]
    sell_book = books[spcx.sell_exchange][(spcx.base, spcx.quote)]

    small = analyze_opportunity(spcx, buy_book, sell_book, Fees(), budget=10_000)
    assert small.realized_profit > 0
    assert small.is_budget_limited is True              # 10k can't exhaust the gap
    assert small.max_capital_usable > small.budget      # more capital would earn more
    assert small.max_profit > small.realized_profit


def test_phantom_opportunity_dies_in_depth():
    # SOL shows a top-of-book gap but fees + slippage make it unprofitable.
    books = load_books_from_fixture(BOOKS)
    tickers = load_from_fixture(TICKERS)
    opps = find_opportunities(tickers["binance"], tickers["gate"], "binance", "gate",
                              Fees(), min_net_spread_pct=-100)
    sol = next(o for o in opps if o.base == "SOL")
    buy_book = books[sol.buy_exchange][(sol.base, sol.quote)]
    sell_book = books[sol.sell_exchange][(sol.base, sol.quote)]
    a = analyze_opportunity(sol, buy_book, sell_book, Fees(), budget=10_000)
    assert a.realized_profit == 0.0
