"""Depth-aware profitability analysis for arbitrage opportunities.

For each candidate market this answers the questions that actually matter before
committing capital:

* **Max profitable size** — how much can you trade before slippage closes the
  gap? (i.e. deploy capital until the marginal unit nets zero after fees.)
* **Realized profit** — at your capital budget, what is the net USD/quote profit
  and the realized % return, *after* slippage and fees on both legs?
* **Capital efficiency** — are you depth-limited (the book runs dry / converges)
  or budget-limited (you have more capital than the gap can absorb)?
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .core import Fees, Opportunity
from .depth import Fill, OrderBook, match_books


@dataclass
class ProfitAnalysis:
    opp: Opportunity
    # At the user's capital budget:
    fill_at_budget: Fill
    # Best case ignoring budget (depth-limited maximum):
    fill_unbounded: Fill
    budget: float

    @property
    def symbol_label(self) -> str:
        return self.opp.symbol_label

    @property
    def realized_profit(self) -> float:
        return self.fill_at_budget.profit

    @property
    def realized_pct(self) -> float:
        return self.fill_at_budget.net_pct

    @property
    def max_profit(self) -> float:
        return self.fill_unbounded.profit

    @property
    def max_capital_usable(self) -> float:
        """Capital the gap can absorb before it stops being profitable."""
        return self.fill_unbounded.quote_spent

    @property
    def is_budget_limited(self) -> bool:
        """True if more capital would still earn more (gap not exhausted)."""
        return self.fill_at_budget.capital_capped


def analyze_opportunity(opp: Opportunity, buy_book: OrderBook, sell_book: OrderBook,
                        fees: Fees, budget: float) -> ProfitAnalysis:
    """Simulate the trade at ``budget`` and at unlimited capital (depth-limited)."""
    buy_fee = fees.for_exchange(opp.buy_exchange)
    sell_fee = fees.for_exchange(opp.sell_exchange)

    fill_budget = match_books(buy_book.asks, sell_book.bids, buy_fee, sell_fee, max_quote=budget)
    fill_max = match_books(buy_book.asks, sell_book.bids, buy_fee, sell_fee, max_quote=math.inf)
    return ProfitAnalysis(opp=opp, fill_at_budget=fill_budget, fill_unbounded=fill_max, budget=budget)


def estimate_daily_profit(analysis: ProfitAnalysis, cycles_per_day: int) -> float:
    """Naive extrapolation: realized profit per execution times assumed cycles/day.

    This is intentionally simple — real throughput depends on how often the gap
    reappears and how fast you can rebalance inventory. Treat as an upper-bound
    sketch, not a forecast.
    """
    return analysis.realized_profit * max(cycles_per_day, 0)
