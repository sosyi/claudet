"""Command-line interface for the Binance <-> Gate spot arbitrage scanner.

Examples
--------
Scan every USDT market once and show the 25 widest net spreads::

    python -m arb --quote USDT --top 25

Only show markets where buying on one venue and selling on the other nets at
least 0.5% after fees::

    python -m arb --min-net-spread 0.5

Continuously refresh every 10 seconds::

    python -m arb --watch 10 --min-net-spread 0.3

See the output format without network access (uses bundled sample data)::

    python -m arb --demo
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from .analyze import ProfitAnalysis, analyze_opportunity
from .core import Fees, Opportunity, find_opportunities
from .depth import (
    OrderBook,
    fetch_binance_book,
    fetch_gate_book,
    load_books_from_fixture,
)
from .exchanges import BinanceClient, FetchError, GateClient, Symbol, Ticker, load_from_fixture
from .report import build_report

_FIXTURE = os.path.join(os.path.dirname(__file__), "data", "sample_tickers.json")
_BOOK_FIXTURE = os.path.join(os.path.dirname(__file__), "data", "sample_orderbooks.json")


def _gather_books(timeout: float) -> Dict[str, Dict[Symbol, Ticker]]:
    """Fetch both exchanges in parallel; surface a clear error if either fails."""
    binance, gate = BinanceClient(timeout=timeout), GateClient(timeout=timeout)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_b = pool.submit(binance.fetch)
        fut_g = pool.submit(gate.fetch)
        return {"binance": fut_b.result(), "gate": fut_g.result()}


def _format_table(opps: List[Opportunity], top: int) -> str:
    rows = opps[:top]
    if not rows:
        return "No markets matched the current filters."
    headers = ["SYMBOL", "BUY @", "PRICE", "SELL @", "PRICE", "GROSS%", "NET%", "MIN VOL(quote)"]
    table = [headers]
    for o in rows:
        table.append([
            o.symbol_label,
            o.buy_exchange,
            f"{o.buy_price:.6g}",
            o.sell_exchange,
            f"{o.sell_price:.6g}",
            f"{o.gross_spread_pct:+.3f}",
            f"{o.net_spread_pct:+.3f}",
            f"{o.min_quote_volume:,.0f}",
        ])
    widths = [max(len(r[i]) for r in table) for i in range(len(headers))]
    lines = []
    for ri, row in enumerate(table):
        cells = []
        for ci, cell in enumerate(row):
            cells.append(cell.ljust(widths[ci]) if ci in (0, 1, 3) else cell.rjust(widths[ci]))
        lines.append("  ".join(cells))
        if ri == 0:
            lines.append("  ".join("-" * widths[ci] for ci in range(len(headers))))
    return "\n".join(lines)


def _emit(opps: List[Opportunity], fmt: str, top: int) -> None:
    if fmt == "json":
        print(json.dumps([o.to_dict() for o in opps[:top]], indent=2))
    elif fmt == "csv":
        print("symbol,buy_exchange,buy_price,sell_exchange,sell_price,gross_pct,net_pct,min_quote_volume")
        for o in opps[:top]:
            print(f"{o.symbol_label},{o.buy_exchange},{o.buy_price},{o.sell_exchange},"
                  f"{o.sell_price},{o.gross_spread_pct:.4f},{o.net_spread_pct:.4f},{o.min_quote_volume:.2f}")
    else:
        print(_format_table(opps, top))


def _run_once(args, books: Dict[str, Dict[Symbol, Ticker]]) -> List[Opportunity]:
    fees = Fees(binance=args.binance_fee, gate=args.gate_fee)
    return find_opportunities(
        books["binance"], books["gate"],
        name_a="binance", name_b="gate",
        fees=fees,
        min_net_spread_pct=args.min_net_spread,
        min_quote_volume=args.min_volume,
        quote_filter=args.quote,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="arb",
        description="Find spot-price arbitrage opportunities between Binance and Gate.io.",
    )
    p.add_argument("--quote", default=None, help="Only compare markets with this quote currency, e.g. USDT.")
    p.add_argument("--min-net-spread", type=float, default=0.1,
                   help="Minimum after-fee spread in %% to report (default 0.1).")
    p.add_argument("--min-volume", type=float, default=0.0,
                   help="Minimum 24h quote volume on the thinner market (liquidity filter).")
    p.add_argument("--top", type=int, default=25, help="Show at most N opportunities (default 25).")
    p.add_argument("--binance-fee", type=float, default=0.001, help="Binance taker fee fraction (default 0.001).")
    p.add_argument("--gate-fee", type=float, default=0.002, help="Gate taker fee fraction (default 0.002).")
    p.add_argument("--format", choices=["table", "json", "csv"], default="table", help="Output format.")
    p.add_argument("--timeout", type=float, default=15.0, help="Per-request HTTP timeout in seconds.")
    p.add_argument("--watch", type=float, default=0.0,
                   help="Refresh every N seconds instead of running once (Ctrl-C to stop).")
    p.add_argument("--demo", action="store_true",
                   help="Use bundled sample data instead of the network (for testing/offline).")

    g = p.add_argument_group("profit analysis (depth-aware)")
    g.add_argument("--analyze", action="store_true",
                   help="Fetch order-book depth for top candidates and analyze realized profit after slippage.")
    g.add_argument("--capital", type=float, default=10000.0,
                   help="Capital budget per trade in quote currency for analysis (default 10000).")
    g.add_argument("--depth-limit", type=int, default=100,
                   help="Order-book levels to fetch per market when analyzing (default 100).")
    g.add_argument("--analyze-top", type=int, default=15,
                   help="Number of top candidates to deep-analyze with depth (default 15).")
    g.add_argument("--cycles-per-day", type=int, default=20,
                   help="Assumed executions/day for the daily-profit sketch (default 20).")
    g.add_argument("--report", default=None,
                   help="Write the markdown profit report to this file (implies --analyze).")
    return p


def _fetch_book(exchange: str, base: str, quote: str, limit: int, timeout: float) -> OrderBook:
    if exchange == "binance":
        return fetch_binance_book(base, quote, limit=limit, timeout=timeout)
    return fetch_gate_book(base, quote, limit=limit, timeout=timeout)


def _run_analysis(args, opps: List[Opportunity]) -> List[ProfitAnalysis]:
    """Pull depth for the top candidates and compute depth-aware profit."""
    fees = Fees(binance=args.binance_fee, gate=args.gate_fee)
    candidates = opps[: args.analyze_top]
    analyses: List[ProfitAnalysis] = []

    if args.demo:
        books = load_books_from_fixture(_BOOK_FIXTURE)
        for o in candidates:
            buy_book = books.get(o.buy_exchange, {}).get((o.base, o.quote))
            sell_book = books.get(o.sell_exchange, {}).get((o.base, o.quote))
            if buy_book and sell_book:
                analyses.append(analyze_opportunity(o, buy_book, sell_book, fees, args.capital))
        return analyses

    # Live: fetch the two books for each candidate in parallel.
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = []
        for o in candidates:
            fb = pool.submit(_fetch_book, o.buy_exchange, o.base, o.quote, args.depth_limit, args.timeout)
            fs = pool.submit(_fetch_book, o.sell_exchange, o.base, o.quote, args.depth_limit, args.timeout)
            jobs.append((o, fb, fs))
        for o, fb, fs in jobs:
            try:
                buy_book, sell_book = fb.result(), fs.result()
            except FetchError as exc:
                print(f"warn: skipping {o.symbol_label}: {exc}", file=sys.stderr)
                continue
            analyses.append(analyze_opportunity(o, buy_book, sell_book, fees, args.capital))
    return analyses


def _run_analyze_and_report(args) -> int:
    fees = Fees(binance=args.binance_fee, gate=args.gate_fee)
    try:
        books = load_from_fixture(_FIXTURE) if args.demo else _gather_books(args.timeout)
    except FetchError as exc:
        msg = str(exc).lower()
        print(f"error: {exc}", file=sys.stderr)
        if any(s in msg for s in ("not in allowlist", "reach", "forbidden", "http 403", "http 451")):
            print("hint: this environment may block exchange APIs. Allow api.binance.com and "
                  "api.gateio.ws in your network egress settings, or run with --demo.", file=sys.stderr)
        return 2

    # Stage 1: cheap top-of-book scan to pick candidates worth deep-analyzing.
    opps = find_opportunities(
        books["binance"], books["gate"], "binance", "gate", fees,
        min_net_spread_pct=args.min_net_spread, min_quote_volume=args.min_volume,
        quote_filter=args.quote,
    )
    if not opps:
        print("No candidate markets cleared the top-of-book filters; nothing to analyze.", file=sys.stderr)
        return 0

    # Stage 2: depth-aware profit analysis on the top candidates.
    analyses = _run_analysis(args, opps)
    if not analyses:
        print("Could not retrieve order-book depth for any candidate.", file=sys.stderr)
        return 2

    data_source = "内置样例盘口 (--demo)" if args.demo else "Binance + Gate.io 实时盘口"
    report = build_report(analyses, fees, args.capital, args.cycles_per_day, data_source)
    print(report)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print(f"\n[报告已写入 {args.report}]", file=sys.stderr)
    return 0


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.analyze or args.report:
        return _run_analyze_and_report(args)

    def scan_and_print() -> int:
        try:
            books = load_from_fixture(_FIXTURE) if args.demo else _gather_books(args.timeout)
        except FetchError as exc:
            msg = str(exc).lower()
            print(f"error: {exc}", file=sys.stderr)
            if any(s in msg for s in ("not in allowlist", "reach", "forbidden", "http 403", "http 451")):
                print("hint: this environment may block exchange APIs. Allow api.binance.com and "
                      "api.gateio.ws in your network egress settings, or run with --demo.", file=sys.stderr)
            return 2
        opps = _run_once(args, books)
        n_common = len(set(books["binance"]) & set(books["gate"]))
        if args.format == "table":
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"# {stamp}  common markets: {n_common}  reported: {min(len(opps), args.top)}  "
                  f"(fees binance={args.binance_fee:.3%} gate={args.gate_fee:.3%})")
        _emit(opps, args.format, args.top)
        return 0

    if args.watch and args.watch > 0:
        try:
            while True:
                if args.format == "table":
                    print("\033[2J\033[H", end="")  # clear screen
                rc = scan_and_print()
                if rc != 0:
                    return rc
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nstopped.", file=sys.stderr)
            return 0
    return scan_and_print()


if __name__ == "__main__":
    raise SystemExit(main())
