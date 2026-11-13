"""管理口径指标计算。

所有指标均为确定性公式，可直接用 CSV 复核：

- 本月确认收入 = Σ本月确认收入（退款/折让红字冲减）
- 本月毛利 = 本月确认收入 - 本月成本
- 毛利率 = 本月毛利 / 本月确认收入
- 本月回款 = Σ本月回款（实际到账，退款为负）
- 回款率 = 本月回款 / 本月确认收入
- 期末应收 = Σ应收余额（截至 2026-10-31）
- DSO（回款周期）= 期末应收 / 本月确认收入 × 31
- 账龄 = 2026-10-31 - 开票日；逾期天数 = max(0, 账龄 - 约定账期)
"""

from __future__ import annotations

import calendar
from datetime import date

from .config import AGING_BUCKETS, AS_OF
from .models import ContractMetric, Metrics, RawRow


def _aging_bucket(aging_days: int) -> str:
    for label, low, high in AGING_BUCKETS:
        if low <= aging_days <= high:
            return label
    return ">180"


def compute_metrics(rows: list[RawRow], as_of: date = AS_OF) -> Metrics:
    revenue = round(sum(r.revenue for r in rows), 2)
    cost = round(sum(r.cost for r in rows), 2)
    cash = round(sum(r.cash for r in rows), 2)
    ar = round(sum(r.ar_balance for r in rows), 2)
    days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
    dso = round(ar / revenue * days_in_month, 2) if revenue else 0.0
    collection_rate = round(cash / revenue, 4) if revenue else 0.0

    aging_buckets = {label: 0.0 for label, _, _ in AGING_BUCKETS}
    top_ar: list[dict] = []
    overdue_ar = 0.0
    by_contract: list[ContractMetric] = []

    for row in rows:
        aging_days = (as_of - row.invoice_date).days
        if row.ar_balance > 0:
            aging_buckets[_aging_bucket(aging_days)] += row.ar_balance
            overdue_days = max(0, aging_days - row.terms_days)
            if overdue_days > 0:
                overdue_ar += row.ar_balance
            top_ar.append(
                {
                    "contract_id": row.contract_id,
                    "customer": row.customer,
                    "invoice_date": row.invoice_date.isoformat(),
                    "terms_days": row.terms_days,
                    "aging_days": aging_days,
                    "overdue_days": overdue_days,
                    "ar": row.ar_balance,
                }
            )
        if row.revenue != 0:
            gp = row.revenue - row.cost
            by_contract.append(
                ContractMetric(
                    contract_id=row.contract_id,
                    customer=row.customer,
                    revenue=row.revenue,
                    cost=row.cost,
                    gross_profit=gp,
                    gross_margin=gp / row.revenue if row.revenue else 0.0,
                )
            )

    top_ar.sort(key=lambda item: item["ar"], reverse=True)
    return Metrics(
        as_of=as_of,
        days_in_month=days_in_month,
        revenue=revenue,
        cost=cost,
        cash=cash,
        ar=ar,
        rows=len(rows),
        customers=len({r.customer for r in rows}),
        aging_buckets={label: round(value, 2) for label, value in aging_buckets.items()},
        by_contract=sorted(by_contract, key=lambda c: c.revenue, reverse=True),
        top_ar=top_ar[:10],
        overdue_ar=round(overdue_ar, 2),
        dso_days=dso,
        collection_rate=collection_rate,
    )


def formula_sheet(as_of: date = AS_OF) -> list[dict[str, str]]:
    days = calendar.monthrange(as_of.year, as_of.month)[1]
    return [
        {"指标": "本月确认收入", "公式": "Σ 本月确认收入（退款/折让红字冲减）"},
        {"指标": "本月毛利", "公式": "本月确认收入 - 本月成本"},
        {"指标": "毛利率", "公式": "本月毛利 / 本月确认收入"},
        {"指标": "本月回款", "公式": "Σ 本月回款（实际到账，退款为负）"},
        {"指标": "回款率", "公式": "本月回款 / 本月确认收入"},
        {"指标": "期末应收", "公式": "Σ 截至 2026-10-31 的应收余额"},
        {"指标": "回款周期 DSO", "公式": f"期末应收 / 本月确认收入 × {days}"},
        {"指标": "账龄", "公式": f"{as_of.isoformat()} - 开票日"},
        {"指标": "逾期天数", "公式": "max(0, 账龄 - 约定账期)"},
    ]
