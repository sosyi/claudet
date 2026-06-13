"""Unit tests for the arbitrage core logic. Run with: python -m pytest -q"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from arb.core import Fees, find_opportunities
from arb.exchanges import Ticker, load_from_fixture

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "arb", "data", "sample_tickers.json")


def _book(*tickers):
    return {t.symbol: t for t in tickers}


def test_spcx_example_matches_user_scenario():
    binance = _book(Ticker("SPCX", "USDT", bid=163.30, ask=163.44, last=163.37, quote_volume=2_450_000))
    gate = _book(Ticker("SPCX", "USDT", bid=161.20, ask=161.34, last=161.27, quote_volume=540_000))

    opps = find_opportunities(binance, gate, "binance", "gate", Fees(), min_net_spread_pct=-100)
    assert len(opps) == 1
    o = opps[0]
    # Buy cheap on Gate, sell dear on Binance.
    assert o.buy_exchange == "gate" and o.sell_exchange == "binance"
    assert o.buy_price == 161.34 and o.sell_price == 163.30
    assert round(o.gross_spread_pct, 2) == 1.21
    assert o.net_spread_pct < o.gross_spread_pct  # fees reduce it


def test_no_opportunity_when_books_identical():
    t = Ticker("BTC", "USDT", bid=64000, ask=64001, last=64000.5, quote_volume=1_000_000)
    opps = find_opportunities(_book(t), _book(t), "binance", "gate", Fees(), min_net_spread_pct=0.0)
    # Identical books cannot beat fees, so nothing is reported above the 0% floor.
    assert opps == []


def test_min_net_spread_threshold_filters():
    binance = _book(Ticker("X", "USDT", bid=100, ask=100.1, last=100, quote_volume=1e9))
    gate = _book(Ticker("X", "USDT", bid=101, ask=101.1, last=101, quote_volume=1e9))
    # A ~0.9% net edge exists; a 5% threshold must reject it.
    assert find_opportunities(binance, gate, "binance", "gate", Fees(), min_net_spread_pct=5.0) == []
    assert find_opportunities(binance, gate, "binance", "gate", Fees(), min_net_spread_pct=0.5)


def test_volume_filter_uses_thinner_market():
    binance = _book(Ticker("Y", "USDT", bid=10, ask=10.0, last=10, quote_volume=1e9))
    gate = _book(Ticker("Y", "USDT", bid=11, ask=11.0, last=11, quote_volume=1000))
    # Thin side has only 1000 quote volume, so a 1e6 floor removes it.
    assert find_opportunities(binance, gate, "binance", "gate", Fees(),
                              min_net_spread_pct=-100, min_quote_volume=1_000_000) == []


def test_quote_filter():
    binance = _book(
        Ticker("Z", "USDT", bid=1, ask=1.0, last=1, quote_volume=1e9),
        Ticker("Z", "BTC", bid=1, ask=1.0, last=1, quote_volume=1e9),
    )
    gate = _book(
        Ticker("Z", "USDT", bid=1.1, ask=1.1, last=1.1, quote_volume=1e9),
        Ticker("Z", "BTC", bid=1.1, ask=1.1, last=1.1, quote_volume=1e9),
    )
    only_usdt = find_opportunities(binance, gate, "binance", "gate", Fees(),
                                   min_net_spread_pct=-100, quote_filter="USDT")
    assert all(o.quote == "USDT" for o in only_usdt) and len(only_usdt) == 1


def test_fixture_loads_and_scans():
    books = load_from_fixture(FIXTURE)
    opps = find_opportunities(books["binance"], books["gate"], "binance", "gate",
                              Fees(), min_net_spread_pct=0.0)
    symbols = {o.symbol_label for o in opps}
    assert "SPCX/USDT" in symbols
