"""Order-book depth fetching and execution simulation.

Top-of-book spread (in ``core.py``) only tells you a gap *exists*. To know how
much profit you can actually capture, you have to model **slippage**: as you buy
on the cheap venue you eat into deeper, pricier ask levels, and as you sell on
the dear venue you eat into deeper, cheaper bid levels. The two prices converge,
and at some size the trade stops being profitable after fees.

This module fetches L2 depth from each exchange and simulates walking both books
at once to find the realized profit for a given capital, and the *maximum*
profitable size.
"""

from __future__ import annotations

import json
import math
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .exchanges import Symbol, _http_get_json, _to_float

# A price level: (price, quantity_in_base_asset).
Level = Tuple[float, float]


@dataclass
class OrderBook:
    base: str
    quote: str
    bids: List[Level] = field(default_factory=list)  # descending price (best first)
    asks: List[Level] = field(default_factory=list)  # ascending price (best first)

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0


def fetch_binance_book(base: str, quote: str, limit: int = 100,
                       base_url: str = "https://api.binance.com", timeout: float = 15.0) -> OrderBook:
    symbol = f"{base}{quote}".upper()
    url = f"{base_url}/api/v3/depth?symbol={symbol}&limit={limit}"
    data = _http_get_json(url, timeout)
    return OrderBook(
        base=base.upper(), quote=quote.upper(),
        bids=[(_to_float(p), _to_float(q)) for p, q in data.get("bids", [])],
        asks=[(_to_float(p), _to_float(q)) for p, q in data.get("asks", [])],
    )


def fetch_gate_book(base: str, quote: str, limit: int = 100,
                    base_url: str = "https://api.gateio.ws/api/v4", timeout: float = 15.0) -> OrderBook:
    pair = f"{base}_{quote}".upper()
    qs = urllib.parse.urlencode({"currency_pair": pair, "limit": limit})
    data = _http_get_json(f"{base_url}/spot/order_book?{qs}", timeout)
    return OrderBook(
        base=base.upper(), quote=quote.upper(),
        bids=[(_to_float(p), _to_float(q)) for p, q in data.get("bids", [])],
        asks=[(_to_float(p), _to_float(q)) for p, q in data.get("asks", [])],
    )


def load_books_from_fixture(path: str) -> Dict[str, Dict[Symbol, OrderBook]]:
    """Load L2 order books from a JSON fixture (used by --demo analyze).

    Shape: ``{"binance": {"BTC_USDT": {"bids": [[p,q],...], "asks": [...]}}, ...}``
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out: Dict[str, Dict[Symbol, OrderBook]] = {}
    for exch, markets in raw.items():
        books: Dict[Symbol, OrderBook] = {}
        for pair, sides in markets.items():
            base, quote = pair.rsplit("_", 1)
            books[(base.upper(), quote.upper())] = OrderBook(
                base=base.upper(), quote=quote.upper(),
                bids=[(_to_float(p), _to_float(q)) for p, q in sides.get("bids", [])],
                asks=[(_to_float(p), _to_float(q)) for p, q in sides.get("asks", [])],
            )
        out[exch] = books
    return out


@dataclass
class Fill:
    """Result of simulating a matched buy/sell across two books."""

    base_volume: float = 0.0     # asset bought on cheap side / sold on dear side
    quote_spent: float = 0.0     # capital deployed buying (includes buy fee)
    quote_received: float = 0.0  # proceeds from selling (net of sell fee)
    levels_used: int = 0         # how many price-level pairings were consumed
    capital_capped: bool = False # True if we stopped because we hit max_quote

    @property
    def profit(self) -> float:
        return self.quote_received - self.quote_spent

    @property
    def net_pct(self) -> float:
        return (self.profit / self.quote_spent * 100.0) if self.quote_spent > 0 else 0.0

    @property
    def avg_buy_price(self) -> float:
        return (self.quote_spent / self.base_volume) if self.base_volume > 0 else 0.0

    @property
    def avg_sell_price(self) -> float:
        return (self.quote_received / self.base_volume) if self.base_volume > 0 else 0.0


def match_books(buy_asks: List[Level], sell_bids: List[Level],
                buy_fee: float, sell_fee: float,
                max_quote: float = math.inf) -> Fill:
    """Greedily match a buy book against a sell book while it stays profitable.

    We pair the cheapest remaining ask (where we buy) with the highest remaining
    bid (where we sell), consume the smaller quantity, and advance. We stop when
    the next pairing is no longer profitable after fees, when a book runs out, or
    when ``max_quote`` of capital has been deployed.

    ``quote_spent`` includes the buy-side taker fee; ``quote_received`` is net of
    the sell-side taker fee — so ``Fill.profit`` is realized net profit.
    """
    fill = Fill()
    ai = bi = 0
    ask_rem = buy_asks[ai][1] if buy_asks else 0.0
    bid_rem = sell_bids[bi][1] if sell_bids else 0.0

    while ai < len(buy_asks) and bi < len(sell_bids):
        ap, _ = buy_asks[ai]
        bp, _ = sell_bids[bi]

        cost_per = ap * (1.0 + buy_fee)       # effective spend per base unit
        revenue_per = bp * (1.0 - sell_fee)   # effective proceeds per base unit
        if revenue_per <= cost_per:
            break  # marginal unit no longer profitable; we're done

        qty = min(ask_rem, bid_rem)

        # Respect the capital cap on buy-side spend.
        if math.isfinite(max_quote):
            remaining_budget = max_quote - fill.quote_spent
            if remaining_budget <= 0:
                fill.capital_capped = True
                break
            max_qty_by_budget = remaining_budget / cost_per
            if max_qty_by_budget < qty:
                qty = max_qty_by_budget
                fill.capital_capped = True

        fill.base_volume += qty
        fill.quote_spent += qty * cost_per
        fill.quote_received += qty * revenue_per
        fill.levels_used += 1

        if fill.capital_capped:
            break

        ask_rem -= qty
        bid_rem -= qty
        if ask_rem <= 1e-18:
            ai += 1
            ask_rem = buy_asks[ai][1] if ai < len(buy_asks) else 0.0
        if bid_rem <= 1e-18:
            bi += 1
            bid_rem = sell_bids[bi][1] if bi < len(sell_bids) else 0.0

    return fill
