"""Cross-exchange spot arbitrage logic.

Given top-of-book quotes for the same market on two exchanges, we ask: can I buy
on the cheaper venue and simultaneously sell on the dearer one for a net profit
after trading fees?

The realistic cross-exchange model is *inventory-balanced*: you hold both the
base asset and the quote currency on both exchanges, so an opportunity is
executed by buying on one side and selling on the other at the same instant —
no on-chain transfer is needed in the moment. Withdrawal/transfer cost only
matters when you periodically rebalance, so it is reported separately rather
than baked into every spread.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple

from .exchanges import Symbol, Ticker


@dataclass
class Fees:
    """Per-exchange taker fee fractions (0.001 == 0.1%)."""

    binance: float = 0.001  # Binance spot taker default
    gate: float = 0.002  # Gate spot taker default (without GT discount)

    def for_exchange(self, name: str) -> float:
        return {"binance": self.binance, "gate": self.gate}.get(name, 0.0)


@dataclass
class Opportunity:
    base: str
    quote: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float          # ask on the buy exchange (what you pay)
    sell_price: float         # bid on the sell exchange (what you receive)
    gross_spread_pct: float   # before fees
    net_spread_pct: float     # after taker fees on both legs
    min_quote_volume: float   # liquidity of the thinner of the two markets

    @property
    def symbol_label(self) -> str:
        return f"{self.base}/{self.quote}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["symbol"] = self.symbol_label
        return d


def _net_spread(buy_ask: float, sell_bid: float, buy_fee: float, sell_fee: float) -> Tuple[float, float]:
    """Return (gross_pct, net_pct) for buying at ``buy_ask`` and selling at ``sell_bid``."""
    gross = (sell_bid - buy_ask) / buy_ask * 100.0
    cost = buy_ask * (1.0 + buy_fee)
    revenue = sell_bid * (1.0 - sell_fee)
    net = (revenue - cost) / cost * 100.0
    return gross, net


def find_opportunities(
    book_a: Dict[Symbol, Ticker],
    book_b: Dict[Symbol, Ticker],
    name_a: str,
    name_b: str,
    fees: Fees,
    min_net_spread_pct: float = 0.0,
    min_quote_volume: float = 0.0,
    quote_filter: str | None = None,
) -> List[Opportunity]:
    """Compare two exchange books and return profitable directions, sorted best-first.

    For every market listed on *both* exchanges we evaluate both directions
    (buy A / sell B, and buy B / sell A) and keep whichever — if either — clears
    ``min_net_spread_pct`` after fees.
    """
    opportunities: List[Opportunity] = []
    common = set(book_a) & set(book_b)

    fee_a = fees.for_exchange(name_a)
    fee_b = fees.for_exchange(name_b)

    for sym in common:
        base, quote = sym
        if quote_filter and quote != quote_filter.upper():
            continue
        ta, tb = book_a[sym], book_b[sym]
        liquidity = min(ta.quote_volume, tb.quote_volume)
        if liquidity < min_quote_volume:
            continue

        # Direction 1: buy on A (pay A.ask), sell on B (receive B.bid).
        gross1, net1 = _net_spread(ta.ask, tb.bid, fee_a, fee_b)
        # Direction 2: buy on B (pay B.ask), sell on A (receive A.bid).
        gross2, net2 = _net_spread(tb.ask, ta.bid, fee_b, fee_a)

        if net1 >= net2:
            best = Opportunity(
                base=base, quote=quote,
                buy_exchange=name_a, sell_exchange=name_b,
                buy_price=ta.ask, sell_price=tb.bid,
                gross_spread_pct=gross1, net_spread_pct=net1,
                min_quote_volume=liquidity,
            )
        else:
            best = Opportunity(
                base=base, quote=quote,
                buy_exchange=name_b, sell_exchange=name_a,
                buy_price=tb.ask, sell_price=ta.bid,
                gross_spread_pct=gross2, net_spread_pct=net2,
                min_quote_volume=liquidity,
            )

        if best.net_spread_pct >= min_net_spread_pct:
            opportunities.append(best)

    opportunities.sort(key=lambda o: o.net_spread_pct, reverse=True)
    return opportunities
