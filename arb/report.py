"""Render a markdown profitability report from depth-aware analyses."""

from __future__ import annotations

import time
from typing import List

from .analyze import ProfitAnalysis, estimate_daily_profit
from .core import Fees


def _money(x: float, quote: str) -> str:
    if abs(x) >= 1:
        return f"{x:,.2f} {quote}"
    return f"{x:.6g} {quote}"


def build_report(analyses: List[ProfitAnalysis], fees: Fees, budget: float,
                 cycles_per_day: int, data_source: str) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    lines: List[str] = []
    lines.append("# Binance ↔ Gate.io 套利盈利空间分析报告")
    lines.append("")
    lines.append(f"- 生成时间: {ts}")
    lines.append(f"- 数据来源: {data_source}")
    lines.append(f"- 单次投入资金(预算): {budget:,.0f}（计价货币，通常为 USDT）")
    lines.append(f"- 手续费假设: Binance taker {fees.binance:.3%} / Gate taker {fees.gate:.3%}")
    lines.append(f"- 候选市场数: {len(analyses)}")
    lines.append("")

    profitable = [a for a in analyses if a.realized_profit > 0]
    total_realized = sum(a.realized_profit for a in profitable)
    total_max = sum(a.max_profit for a in profitable)
    quote = analyses[0].opp.quote if analyses else "USDT"

    lines.append("## 摘要")
    lines.append("")
    lines.append(f"- 在 {budget:,.0f} 预算下**实际可盈利**的市场: {len(profitable)} 个")
    lines.append(f"- 这些市场单轮合计实现净利润: **{_money(total_realized, quote)}**")
    lines.append(f"- 若资金不设上限(吃满深度)单轮上限净利润: {_money(total_max, quote)}")
    if profitable:
        best = max(profitable, key=lambda a: a.realized_profit)
        lines.append(f"- 单市场最佳: **{best.symbol_label}** — 实现 {_money(best.realized_profit, quote)} "
                     f"(净 {best.realized_pct:+.3f}%)")
        daily = estimate_daily_profit(best, cycles_per_day)
        lines.append(f"  - 若每天可执行 {cycles_per_day} 轮, 该市场理论日盈利上限 ≈ {_money(daily, quote)}（粗略外推）")
    lines.append("")

    lines.append("## 明细（按本预算下实现净利润排序）")
    lines.append("")
    lines.append("| 市场 | 买入@ | 卖出@ | 顶档毛差% | 预算内净利润 | 预算内净% | 可吃下资金 | 资金上限净利润 | 状态 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for a in sorted(analyses, key=lambda x: x.realized_profit, reverse=True):
        o = a.opp
        status = "受资金限制" if a.is_budget_limited else "受深度限制"
        if a.realized_profit <= 0:
            status = "扣费后不盈利"
        lines.append(
            f"| {o.symbol_label} | {o.buy_exchange} {o.buy_price:.6g} | {o.sell_exchange} {o.sell_price:.6g} "
            f"| {o.gross_spread_pct:+.3f} | {_money(a.realized_profit, quote)} | {a.realized_pct:+.3f} "
            f"| {_money(a.max_capital_usable, quote)} | {_money(a.max_profit, quote)} | {status} |"
        )
    lines.append("")

    lines.append("## 怎么读这张表")
    lines.append("")
    lines.append("- **顶档毛差%**: 只看最优买一/卖一的价差，未扣费、未考虑成交量——只能说明“有缝”。")
    lines.append("- **预算内净利润 / 净%**: 用本预算真实地“吃”订单簿（逐档滑点）后，扣双边手续费的净结果。这才是真实盈利空间。")
    lines.append("- **可吃下资金**: 价差被滑点吃平之前，这个机会最多能容纳多少资金。超过这个数，多投的钱赚不到边际利润。")
    lines.append("- **状态 = 受资金限制**: 缝还没吃平你的钱就花光了 → 加大资金还能多赚。")
    lines.append("- **状态 = 受深度限制**: 你的钱没花完缝就被吃平了 → 这个机会的容量上限就这么大。")
    lines.append("- **状态 = 扣费后不盈利**: 顶档看着有差，但扣掉双边手续费后是负的，别碰。")
    lines.append("")

    lines.append("## 重要风险提示")
    lines.append("")
    lines.append("1. **快照即时性**: 报告基于某一时刻的盘口快照，真实价差是毫秒级的，下单前可能已消失。")
    lines.append("2. **执行同时性**: 跨所套利需要两腿几乎同时成交；任一腿滑点/延迟都会侵蚀利润。")
    lines.append("3. **库存与再平衡**: 该模型假设两所都备有资产+资金（库存对冲）。周期性把资产搬回平衡时会产生提币费与到账延迟，本表未计入。")
    lines.append("4. **提币限制**: 代币化股票及部分代币可能限制跨所提币，或在重大事件时暂停交易。")
    lines.append("5. **手续费档位**: 默认费率偏保守；请用 `--binance-fee/--gate-fee` 填你的真实档位，结论会变。")
    lines.append("6. 本工具仅做行情分析与盈利测算，**只读不下单**，自负盈亏。")
    lines.append("")
    return "\n".join(lines)
