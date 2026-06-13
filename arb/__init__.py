"""Binance <-> Gate.io spot arbitrage scanner."""

from .core import Fees, Opportunity, find_opportunities
from .exchanges import BinanceClient, GateClient, Ticker

__all__ = [
    "Fees",
    "Opportunity",
    "find_opportunities",
    "BinanceClient",
    "GateClient",
    "Ticker",
]

__version__ = "1.0.0"
