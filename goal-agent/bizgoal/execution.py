"""模拟执行层。

原型不连接真实 ERP/CRM，动作只做「沙盘执行」：
- 每个动作生成执行回执（成功/失败/已执行）；
- 失败动作不产生效果，交由上层剔除并重规划；
- 回执先落盘再返回，便于断点排查；
- 幂等账本避免重规划时重复执行同一动作。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .chaos import ChaosConfig
from .logging_utils import RunLogger
from .models import Action, Receipt


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_ledger(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def execute_actions(
    actions: list[Action],
    run_dir: Path,
    logger: RunLogger,
    chaos: ChaosConfig,
    ledger: dict[str, str] | None = None,
) -> tuple[list[Receipt], dict[str, str]]:
    run_dir = Path(run_dir)
    ledger = ledger or load_ledger(run_dir / "execution_ledger.json")
    receipts_path = run_dir / "action_receipts.json"
    receipts: list[Receipt] = []

    for action in actions:
        if ledger.get(action.id) == "executed":
            receipts.append(
                Receipt(
                    action_id=action.id,
                    title=action.title,
                    status="already_executed",
                    executed_at=_now(),
                    amount=round(action.expected_cash if action.kind == "collection" else action.delta_gp, 2),
                    note="幂等账本命中，效果已计入，未重复执行",
                )
            )
            logger.info("execute", "动作已执行过，跳过", action_id=action.id)
            continue
        if action.id in chaos.action_fail_ids:
            receipt = Receipt(
                action_id=action.id,
                title=action.title,
                status="failed",
                executed_at=_now(),
                amount=0.0,
                note="故障注入：模拟审批被拒/客户拒绝回款",
            )
            logger.fallback(
                "execute",
                f"动作 {action.id} 执行失败（故障注入）",
                "剔除失败动作，基于剩余动作重新规划",
                action_id=action.id,
            )
        else:
            amount = action.expected_cash if action.kind == "collection" else action.delta_gp
            receipt = Receipt(
                action_id=action.id,
                title=action.title,
                status="executed",
                executed_at=_now(),
                amount=round(amount, 2),
                note="沙盘执行成功（未写入真实系统）",
            )
            ledger[action.id] = "executed"
            logger.info("execute", "动作执行成功", action_id=action.id, amount=round(amount, 2))
        receipts.append(receipt)
        receipts_path.write_text(
            json.dumps([item.to_dict() for item in receipts], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    (run_dir / "execution_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    receipts_path.write_text(
        json.dumps([item.to_dict() for item in receipts], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipts, ledger


def successful_action_ids(receipts: list[Receipt]) -> set[str]:
    return {r.action_id for r in receipts if r.status in {"executed", "already_executed"}}
