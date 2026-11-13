"""基线诊断：把「差多少」拆成合同、客户、账龄三类可行动线索。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import Goal, Metrics


@dataclass
class Diagnosis:
    margin_gap_pp: float
    margin_gap_amount: float
    ar_target: float
    cash_gap: float
    low_margin_contracts: list[dict[str, Any]] = field(default_factory=list)
    top_cost_contracts: list[dict[str, Any]] = field(default_factory=list)
    aging_gt_180: list[dict[str, Any]] = field(default_factory=list)
    top_ar: list[dict[str, Any]] = field(default_factory=list)
    concentration: float = 0.0
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def diagnose(metrics: Metrics, goal: Goal, aging_gt_180_contracts: set[str]) -> Diagnosis:
    gap_pp = (goal.target_margin - metrics.gross_margin) * 100
    gap_amount = max(0.0, goal.target_margin * metrics.revenue - metrics.gross_profit)
    ar_target = goal.dso_target_days * metrics.revenue / metrics.days_in_month
    cash_gap = max(0.0, metrics.ar - ar_target)

    contracts = [c.to_dict() for c in metrics.by_contract]
    low_margin = sorted(
        (c for c in contracts if c["revenue"] > 0),
        key=lambda c: c["gross_margin"],
    )[:5]
    top_cost = sorted(contracts, key=lambda c: c["cost"], reverse=True)[:5]
    aging_gt_180 = [item for item in metrics.top_ar if item["contract_id"] in aging_gt_180_contracts][:5]
    top_ar = metrics.top_ar[:5]
    total_ar = metrics.ar or 1.0
    concentration = sum(item["ar"] for item in top_ar) / total_ar

    findings: list[str] = []
    findings.append(
        f"毛利率 {metrics.gross_margin:.2%}，距目标 {goal.target_margin:.2%} "
        f"差 {gap_pp:.2f} 个百分点，约 {gap_amount/10_000:.1f} 万元毛利。"
    )
    findings.append(
        f"期末应收 {metrics.ar/10_000:.1f} 万元，DSO {metrics.dso_days:.2f} 天；"
        f"压到 {goal.dso_target_days:.0f} 天需回收约 {cash_gap/10_000:.1f} 万元。"
    )
    if low_margin:
        worst = low_margin[0]
        findings.append(
            f"最低毛利合同 {worst['contract_id']}（{worst['customer']}）"
            f"毛利率 {worst['gross_margin']:.2%}，收入 {worst['revenue']/10_000:.1f} 万元，"
            "是提价/降本的首要对象。"
        )
    high_margin = sorted((c for c in contracts if c["revenue"] > 0), key=lambda c: c["gross_margin"], reverse=True)
    if high_margin:
        best = high_margin[0]
        findings.append(
            f"最高毛利合同 {best['contract_id']}（{best['customer']}）"
            f"毛利率 {best['gross_margin']:.2%}，可作为追加订单的优先客户。"
        )
    if aging_gt_180:
        amount = sum(item["ar"] for item in aging_gt_180)
        findings.append(
            f"账龄超 180 天应收 {amount/10_000:.1f} 万元，"
            f"占期末应收 {amount/total_ar:.1%}，需法务+销售联合清收。"
        )
    findings.append(f"前 5 大应收占期末应收 {concentration:.1%}，回款动作应聚焦头部客户。")

    return Diagnosis(
        margin_gap_pp=round(gap_pp, 2),
        margin_gap_amount=round(gap_amount, 2),
        ar_target=round(ar_target, 2),
        cash_gap=round(cash_gap, 2),
        low_margin_contracts=low_margin,
        top_cost_contracts=top_cost,
        aging_gt_180=aging_gt_180,
        top_ar=top_ar,
        concentration=round(concentration, 4),
        findings=findings,
    )
