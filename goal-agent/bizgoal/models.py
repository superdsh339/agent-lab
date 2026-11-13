"""跨模块共享的数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass
class RawRow:
    customer: str
    contract_id: str
    contract_amount: float
    revenue: float
    cost: float
    cash: float
    invoice_date: date
    terms_days: int
    ar_balance: float
    source_line: int = 0
    # 清洗后的标记，不进入原始 CSV
    adjusted: bool = False
    adjustment_note: str = ""

    def to_csv_dict(self) -> dict[str, Any]:
        return {
            "客户": self.customer,
            "合同号": self.contract_id,
            "合同金额": _fmt_money(self.contract_amount),
            "本月确认收入": _fmt_money(self.revenue),
            "本月成本": _fmt_money(self.cost),
            "本月回款": _fmt_money(self.cash),
            "开票日": self.invoice_date.isoformat(),
            "约定账期": str(self.terms_days),
            "应收余额": _fmt_money(self.ar_balance),
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["开票日"] = self.invoice_date.isoformat()
        data["invoice_date"] = self.invoice_date.isoformat()
        return data


@dataclass
class Totals:
    revenue: float
    cost: float
    cash: float
    ar: float
    rows: int
    customers: int

    @property
    def gross_profit(self) -> float:
        return self.revenue - self.cost

    @property
    def gross_margin(self) -> float:
        return self.gross_profit / self.revenue if self.revenue else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["gross_profit"] = self.gross_profit
        data["gross_margin"] = self.gross_margin
        return data


@dataclass
class Finding:
    code: str
    severity: str  # info / warning / error
    message: str
    contract_id: str = ""
    customer: str = ""
    adjustment: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CleanResult:
    rows: list[RawRow]  # 管理口径（跨期行已冲回）
    raw_rows: list[RawRow]
    findings: list[Finding]
    reported: Totals
    adjusted: Totals
    stats: dict[str, Any]

    @property
    def anomaly_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.code] = counts.get(finding.code, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "reported": self.reported.to_dict(),
            "adjusted": self.adjusted.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
            "anomaly_counts": self.anomaly_counts,
            "stats": self.stats,
        }


@dataclass
class ContractMetric:
    contract_id: str
    customer: str
    revenue: float
    cost: float
    gross_profit: float
    gross_margin: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Metrics:
    as_of: date
    days_in_month: int
    revenue: float
    cost: float
    cash: float
    ar: float
    rows: int
    customers: int
    aging_buckets: dict[str, float]
    by_contract: list[ContractMetric]
    top_ar: list[dict[str, Any]]
    overdue_ar: float
    dso_days: float
    collection_rate: float

    @property
    def gross_profit(self) -> float:
        return self.revenue - self.cost

    @property
    def gross_margin(self) -> float:
        return self.gross_profit / self.revenue if self.revenue else 0.0

    @property
    def low_margin_contracts(self) -> list[ContractMetric]:
        return sorted(
            (c for c in self.by_contract if c.revenue > 0),
            key=lambda c: c.gross_margin,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "days_in_month": self.days_in_month,
            "revenue": self.revenue,
            "cost": self.cost,
            "gross_profit": self.gross_profit,
            "gross_margin": self.gross_margin,
            "cash": self.cash,
            "ar": self.ar,
            "rows": self.rows,
            "customers": self.customers,
            "aging_buckets": self.aging_buckets,
            "by_contract": [c.to_dict() for c in self.by_contract],
            "top_ar": self.top_ar,
            "overdue_ar": self.overdue_ar,
            "dso_days": self.dso_days,
            "collection_rate": self.collection_rate,
        }


@dataclass
class Goal:
    raw_text: str
    margin_delta_pp: float
    dso_target_days: float
    baseline_margin: float
    target_margin: float
    parser: str  # rule / llm
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Action:
    id: str
    kind: str  # margin / collection
    title: str
    rationale: str
    contract_ids: list[str]
    customer_names: list[str]
    owner: str
    due_date: str
    confidence: float
    effort: str
    risk: str
    delta_revenue: float = 0.0
    delta_cost: float = 0.0  # 负数表示降本
    expected_cash: float = 0.0
    assumptions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def delta_gp(self) -> float:
        return self.delta_revenue - self.delta_cost

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["delta_gp"] = self.delta_gp
        return data


@dataclass
class Plan:
    selected: list[Action]
    backups: list[Action]
    target_margin: float
    target_dso_days: float
    forecast: dict[str, float]
    conservative: dict[str, float]
    meets: bool
    gaps: list[str]
    checks: dict[str, Any]
    search_stats: dict[str, Any]
    excluded_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": [a.to_dict() for a in self.selected],
            "backups": [a.to_dict() for a in self.backups],
            "target_margin": self.target_margin,
            "target_dso_days": self.target_dso_days,
            "forecast": self.forecast,
            "conservative": self.conservative,
            "meets": self.meets,
            "gaps": self.gaps,
            "checks": self.checks,
            "search_stats": self.search_stats,
            "excluded_ids": self.excluded_ids,
        }


@dataclass
class Receipt:
    action_id: str
    title: str
    status: str  # executed / failed / already_executed / skipped
    executed_at: str
    amount: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fmt_money(value: float) -> str:
    """CSV 金额保留两位小数，整数值不带 .0。"""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}"
