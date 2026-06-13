"""Exchange clients for Binance and Gate.io spot markets.

Only the public REST endpoints are used, so no API keys are required for
*reading* prices. Everything here relies on the Python standard library so the
tool stays dependency-free and easy to run anywhere.

Each client returns a dict keyed by a normalized ``Symbol`` (base, quote) so the
two exchanges can be matched against each other regardless of how they format
their pair strings ("BTCUSDT" on Binance vs "BTC_USDT" on Gate).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# A normalized market key shared across exchanges: (BASE, QUOTE), upper-cased.
Symbol = Tuple[str, str]

_USER_AGENT = "arb-scanner/1.0 (+https://github.com/sosyi/claudet)"


@dataclass
class Ticker:
    """Top-of-book quote for one market on one exchange.

    ``bid`` is the highest price a buyer will pay (you *sell* into it).
    ``ask`` is the lowest price a seller will accept (you *buy* from it).
    """

    base: str
    quote: str
    bid: float
    ask: float
    last: float
    quote_volume: float  # 24h turnover in the quote currency (liquidity proxy)

    @property
    def symbol(self) -> Symbol:
        return (self.base, self.quote)


class FetchError(RuntimeError):
    """Raised when an exchange endpoint cannot be reached or parsed."""


def _http_get_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        raise FetchError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        raise FetchError(f"Could not reach {url}: {exc.reason}") from exc
    except (ValueError, json.JSONDecodeError) as exc:  # pragma: no cover
        raise FetchError(f"Invalid JSON from {url}: {exc}") from exc


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class BinanceClient:
    """Reads Binance spot top-of-book quotes.

    Binance reports symbols without a separator ("BTCUSDT"), so we first pull
    ``exchangeInfo`` to learn how each symbol splits into base/quote assets,
    then attach live bid/ask from ``bookTicker`` and 24h volume from ``ticker/24hr``.
    """

    name = "binance"

    def __init__(self, base_url: str = "https://api.binance.com", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _exchange_info(self) -> Dict[str, Symbol]:
        data = _http_get_json(f"{self.base_url}/api/v3/exchangeInfo", self.timeout)
        mapping: Dict[str, Symbol] = {}
        for sym in data.get("symbols", []):
            if sym.get("status") != "TRADING":
                continue
            # Spot trading must be permitted.
            perms = sym.get("permissions") or []
            perm_sets = sym.get("permissionSets") or []
            flat_sets = {p for group in perm_sets for p in group}
            if "SPOT" not in perms and "SPOT" not in flat_sets and not sym.get("isSpotTradingAllowed", False):
                continue
            mapping[sym["symbol"]] = (sym["baseAsset"].upper(), sym["quoteAsset"].upper())
        return mapping

    def fetch(self) -> Dict[Symbol, Ticker]:
        symbol_map = self._exchange_info()
        books = _http_get_json(f"{self.base_url}/api/v3/ticker/bookTicker", self.timeout)
        vols = _http_get_json(f"{self.base_url}/api/v3/ticker/24hr", self.timeout)

        vol_by_symbol = {row["symbol"]: _to_float(row.get("quoteVolume")) for row in vols}

        out: Dict[Symbol, Ticker] = {}
        for row in books:
            sym = row.get("symbol")
            if sym not in symbol_map:
                continue
            base, quote = symbol_map[sym]
            bid = _to_float(row.get("bidPrice"))
            ask = _to_float(row.get("askPrice"))
            if bid <= 0 or ask <= 0:
                continue
            out[(base, quote)] = Ticker(
                base=base,
                quote=quote,
                bid=bid,
                ask=ask,
                last=(bid + ask) / 2,
                quote_volume=vol_by_symbol.get(sym, 0.0),
            )
        return out


class GateClient:
    """Reads Gate.io spot top-of-book quotes from a single tickers endpoint.

    Gate pairs are formatted ``BASE_QUOTE`` ("BTC_USDT"), so base/quote split is
    explicit. ``lowest_ask`` / ``highest_bid`` give the top of book directly.
    """

    name = "gate"

    def __init__(self, base_url: str = "https://api.gateio.ws/api/v4", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch(self) -> Dict[Symbol, Ticker]:
        data = _http_get_json(f"{self.base_url}/spot/tickers", self.timeout)
        out: Dict[Symbol, Ticker] = {}
        for row in data:
            pair = row.get("currency_pair", "")
            if "_" not in pair:
                continue
            base, quote = pair.rsplit("_", 1)
            base, quote = base.upper(), quote.upper()
            bid = _to_float(row.get("highest_bid"))
            ask = _to_float(row.get("lowest_ask"))
            last = _to_float(row.get("last"))
            if bid <= 0 or ask <= 0:
                continue
            out[(base, quote)] = Ticker(
                base=base,
                quote=quote,
                bid=bid,
                ask=ask,
                last=last or (bid + ask) / 2,
                quote_volume=_to_float(row.get("quote_volume")),
            )
        return out


def load_from_fixture(path: str) -> Dict[str, Dict[Symbol, Ticker]]:
    """Load sample exchange data from a JSON fixture (used by --demo).

    Fixture shape::

        {"binance": [{"base": "...", "quote": "...", "bid": .., "ask": .., ...}],
         "gate":    [ ... ]}
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    result: Dict[str, Dict[Symbol, Ticker]] = {}
    for exch, rows in raw.items():
        book: Dict[Symbol, Ticker] = {}
        for r in rows:
            t = Ticker(
                base=r["base"].upper(),
                quote=r["quote"].upper(),
                bid=_to_float(r["bid"]),
                ask=_to_float(r["ask"]),
                last=_to_float(r.get("last", (r["bid"] + r["ask"]) / 2)),
                quote_volume=_to_float(r.get("quote_volume", 0.0)),
            )
            book[t.symbol] = t
        result[exch] = book
    return result
