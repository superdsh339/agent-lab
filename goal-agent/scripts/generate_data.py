#!/usr/bin/env python3
"""造数脚本入口：生成 2026-10 经营数据 CSV 与数据清单。

用法：
    python scripts/generate_data.py
    python scripts/generate_data.py --out /tmp/business.csv --manifest /tmp/manifest.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from bizgoal.config import DEFAULT_DATA_PATH, DEFAULT_MANIFEST_PATH  # noqa: E402
from bizgoal.data_gen import generate_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 2026 年 10 月经营演示数据")
    parser.add_argument("--out", default=str(DEFAULT_DATA_PATH))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    args = parser.parse_args()
    manifest = generate_dataset(Path(args.out), Path(args.manifest))
    print(f"已生成 {args.out}")
    print(f"已生成 {args.manifest}")
    print(
        f"行数 {manifest['totals']['rows']}，客户 {manifest['totals']['customers']}，"
        f"收入 {manifest['totals']['revenue']:,.2f}，毛利率 {manifest['totals']['gross_margin']:.2%}"
    )
    for item in manifest["anomalies"]:
        print(f"异常 {item['code']}: {item['contract_id']}（{item['customer']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
