"""演示用故障注入。

只影响本次进程内运行，不修改源数据或远程系统：
- llm-down：模拟模型服务不可用，验证规则降级；
- data-corrupt：生成一份坏数据副本，验证契约校验与失败报告；
- action-fail[:动作ID]：模拟动作执行失败，验证剔除失败动作后的重规划。
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_FAIL_ACTION = "C-AR-0116"


@dataclass
class ChaosConfig:
    llm_down: bool = False
    data_corrupt: bool = False
    action_fail_ids: set[str] = field(default_factory=set)

    @property
    def enabled(self) -> bool:
        return self.llm_down or self.data_corrupt or bool(self.action_fail_ids)

    def describe(self) -> dict:
        return {
            "llm_down": self.llm_down,
            "data_corrupt": self.data_corrupt,
            "action_fail_ids": sorted(self.action_fail_ids),
        }


def parse_chaos(raw: str | None) -> ChaosConfig:
    config = ChaosConfig()
    if not raw:
        return config
    for token in raw.split(","):
        token = token.strip()
        if not token or token == "none":
            continue
        if token == "llm-down":
            config.llm_down = True
        elif token == "data-corrupt":
            config.data_corrupt = True
        elif token == "action-fail":
            config.action_fail_ids.add(DEFAULT_FAIL_ACTION)
        elif token.startswith("action-fail:"):
            action_id = token.split(":", 1)[1].strip()
            if not action_id:
                raise ValueError("action-fail 需要动作 ID，例如 action-fail:C-AR-0116")
            config.action_fail_ids.add(action_id)
        else:
            raise ValueError(f"未知故障注入：{token}")
    return config
