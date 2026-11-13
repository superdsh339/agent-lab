"""CSV 读取与类型校验。"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .config import COLUMNS, CONTRACT_COLUMN, CUSTOMER_COLUMN, DATE_COLUMN, MONEY_COLUMNS
from .errors import DataQualityError
from .models import RawRow


def _parse_money(value: str, column: str) -> float:
    cleaned = value.strip().replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
    if cleaned == "":
        raise ValueError(f"{column} 为空")
    return float(cleaned)


def read_csv_rows(path: Path) -> list[RawRow]:
    path = Path(path)
    if not path.exists():
        raise DataQualityError(f"数据文件不存在：{path}")
    rows: list[RawRow] = []
    issues: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise DataQualityError(f"数据文件没有表头：{path}")
        header = [h.strip() for h in reader.fieldnames]
        missing = [c for c in COLUMNS if c not in header]
        extra = [c for c in header if c not in COLUMNS]
        if missing or extra:
            detail = []
            if missing:
                detail.append(f"缺少列：{'、'.join(missing)}")
            if extra:
                detail.append(f"多余列：{'、'.join(extra)}")
            raise DataQualityError(f"数据契约不匹配（{path.name}）：" + "；".join(detail), detail)
        for line_no, raw in enumerate(reader, start=2):
            raw = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
            if not any((value or "").strip() for value in raw.values()):
                continue
            try:
                customer = (raw[CUSTOMER_COLUMN] or "").strip()
                contract_id = (raw[CONTRACT_COLUMN] or "").strip()
                if not customer or not contract_id:
                    raise ValueError("客户和合同号不能为空")
                money = {column: _parse_money(raw[column] or "", column) for column in MONEY_COLUMNS}
                terms = int(float((raw["约定账期"] or "").strip()))
                invoice_date = date.fromisoformat((raw[DATE_COLUMN] or "").strip())
                rows.append(
                    RawRow(
                        customer=customer,
                        contract_id=contract_id,
                        contract_amount=money["合同金额"],
                        revenue=money["本月确认收入"],
                        cost=money["本月成本"],
                        cash=money["本月回款"],
                        invoice_date=invoice_date,
                        terms_days=terms,
                        ar_balance=money["应收余额"],
                        source_line=line_no,
                    )
                )
            except (ValueError, TypeError) as exc:
                issues.append(f"第 {line_no} 行：{exc}")
                if len(issues) >= 20:
                    issues.append("……更多错误已省略")
                    break
    if issues:
        raise DataQualityError(f"数据解析失败（{len(issues)} 处问题）", issues)
    if not rows:
        raise DataQualityError(f"数据文件为空：{path}")
    return rows
