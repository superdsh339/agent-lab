"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from . import __version__
from .chaos import parse_chaos
from .config import AS_OF, DEFAULT_DATA_PATH, DEFAULT_GOAL, DEFAULT_MANIFEST_PATH
from .data_gen import generate_dataset
from .llm import LLMClient, LLMConfig
from .pipeline import run_pipeline


def _default_out() -> str:
    return str(Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S"))


def _print_summary(result: dict) -> None:
    status_labels = {
        "met": "目标达成",
        "met_after_recovery": "目标达成（经兜底重规划）",
        "partial": "未完全达成",
        "infeasible": "目标不可达",
        "failed": "运行失败",
    }
    print("=" * 64)
    print("经营目标智能体运行摘要")
    print("=" * 64)
    if result.get("status") == "failed":
        print(f"状态：{status_labels.get(result['status'], result['status'])}")
        print(f"错误：{result.get('error', '未知')}")
        for issue in result.get("issues", [])[:8]:
            print(f"  - {issue}")
        print(f"报告：{Path(result['out_dir']) / 'report.md'}")
        print(f"日志：{Path(result['out_dir']) / 'run.log'}")
        return
    goal = result["goal"]
    before = result["metrics_before"]["adjusted"]
    after = result["metrics_after"]["forecast"]
    plan = result["plan"]
    print(f"运行 ID：{result['run_id']}")
    print(f"状态：{status_labels.get(result['status'], result['status'])}")
    print(
        f"基线：毛利率 {before['gross_margin']:.2%} | DSO {before['dso_days']:.2f} 天 | "
        f"应收 {before['ar']/10_000:.2f} 万元"
    )
    print(
        f"目标：毛利率 {goal['target_margin']:.2%}（+{goal['margin_delta_pp']:.0f}pp） | "
        f"DSO ≤ {goal['dso_target_days']:.0f} 天"
    )
    print(
        f"预测：毛利率 {after['gross_margin']:.2%} | DSO {after['dso_days']:.2f} 天 | "
        f"预计回款 {after.get('collected', 0)/10_000:.2f} 万元"
    )
    effective = result.get("execution", {}).get("effective_actions") or []
    if result["status"] == "infeasible":
        print(f"动作：{len(plan['backups'])} 个兼容动作落地仍不足（未执行）")
    elif effective and len(effective) != len(plan["selected"]):
        print(
            f"动作：计划 {len(plan['selected'])} 个，兜底后实际生效 {len(effective)} 个"
            f"（毛利 {sum(1 for a in effective if a['kind']=='margin')} / "
            f"回款 {sum(1 for a in effective if a['kind']=='collection')}）"
        )
    else:
        print(
            f"动作：{len(plan['selected'])} 个（毛利 {sum(1 for a in plan['selected'] if a['kind']=='margin')} / "
            f"回款 {sum(1 for a in plan['selected'] if a['kind']=='collection')}）"
        )
    if result.get("metrics_after", {}).get("gaps"):
        for gap in result["metrics_after"]["gaps"]:
            print(f"缺口：{gap}")
    verification = result.get("verification", [])
    passed = sum(1 for item in verification if item["pass"])
    print(f"自检：{passed}/{len(verification)} 项通过")
    print(f"模型：{result.get('llm_label', '未调用')}")
    fallbacks = result.get("fallbacks", [])
    if fallbacks:
        print(f"兜底：{len(fallbacks)} 次（详见报告第 6 节）")
    print(f"报告：{result.get('report_path', Path(result['out_dir']) / 'report.md')}")
    print(f"日志：{Path(result['out_dir']) / 'run.log'}")


def _cmd_data(args: argparse.Namespace) -> int:
    manifest = generate_dataset(Path(args.out), Path(args.manifest))
    print(f"数据：{args.out}")
    print(f"清单：{args.manifest}")
    print(json.dumps(manifest["totals"], ensure_ascii=False, indent=2))
    print("三类异常：")
    for item in manifest["anomalies"]:
        print(f"  - {item['code']}: {item['contract_id']} ({item['customer']})")
    return 0


def _cmd_run(args: argparse.Namespace, demo: bool = False) -> int:
    try:
        chaos = parse_chaos(args.chaos)
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2
    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        print(f"参数错误：--as-of 需要 YYYY-MM-DD，收到 {args.as_of}", file=sys.stderr)
        return 2
    out = args.out or (str(Path("runs") / "demo") if demo else _default_out())
    if demo and not args.json:
        print("演示模式：正常跑通 + 生成报告；故障演示命令见 docs/录屏脚本.md")
    result = run_pipeline(
        out_dir=out,
        data_path=args.data,
        goal_text=args.goal,
        llm_mode=args.llm,
        chaos=chaos,
        as_of=as_of,
        allow_regenerate=not args.no_regenerate,
    )
    if args.json:
        print(json.dumps(result.get("summary", result), ensure_ascii=False, indent=2))
    else:
        _print_summary(result)
    return int(result.get("exit_code", 5))


def _cmd_llm_check(args: argparse.Namespace) -> int:
    config = LLMConfig.from_env(mode="auto")
    if args.timeout is not None:
        config.timeout = args.timeout
    client = LLMClient(config)
    payload = client.check()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bizgoal",
        description="经营目标智能体原型：把老板口述目标拆成可量化动作并闭环校验。",
    )
    parser.add_argument("--version", action="version", version=f"bizgoal {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_data = sub.add_parser("data", help="生成 2026-10 演示数据与清单")
    p_data.add_argument("--out", default=str(DEFAULT_DATA_PATH))
    p_data.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    p_data.set_defaults(func=_cmd_data)

    p_run = sub.add_parser("run", help="端到端运行")
    p_run.add_argument("--data", default=None, help="CSV 路径；缺省使用 data/business_2026_10.csv")
    p_run.add_argument("--goal", default=DEFAULT_GOAL, help="老板口述目标")
    p_run.add_argument("--out", default=None, help="输出目录")
    p_run.add_argument("--llm", choices=["auto", "off"], default="auto")
    p_run.add_argument("--chaos", default="", help="故障注入：llm-down,data-corrupt,action-fail[:动作ID]")
    p_run.add_argument("--as-of", default=AS_OF.isoformat())
    p_run.add_argument("--no-regenerate", action="store_true", help="默认数据缺失时不自动重造")
    p_run.add_argument("--json", action="store_true", help="输出 summary JSON")
    p_run.set_defaults(func=_cmd_run)

    p_demo = sub.add_parser("demo", help="一键演示（输出到 runs/demo）")
    p_demo.add_argument("--data", default=None)
    p_demo.add_argument("--goal", default=DEFAULT_GOAL)
    p_demo.add_argument("--llm", choices=["auto", "off"], default="auto")
    p_demo.add_argument("--chaos", default="")
    p_demo.add_argument("--as-of", default=AS_OF.isoformat())
    p_demo.add_argument("--no-regenerate", action="store_true")
    p_demo.add_argument("--out", default=str(Path("runs") / "demo"))
    p_demo.add_argument("--json", action="store_true")
    p_demo.set_defaults(func=lambda args: _cmd_run(args, demo=True))

    p_llm = sub.add_parser("llm-check", help="检查国产 API / 私有化模型连通性")
    p_llm.add_argument("--timeout", type=float, default=None)
    p_llm.set_defaults(func=_cmd_llm_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - 最后一层保护
        print(f"运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
