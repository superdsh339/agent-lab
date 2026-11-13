"""结构化运行日志：同时写人读的 run.log 与机读的 events.jsonl。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunLogger:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.log_path = self.run_dir / "run.log"
        self.fallbacks: list[dict[str, Any]] = []

    def _record(self, level: str, stage: str, message: str, data: dict[str, Any] | None) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "level": level,
            "stage": stage,
            "message": message,
            "data": data or {},
        }
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        suffix = ""
        if data:
            parts = []
            for key, value in data.items():
                if isinstance(value, float):
                    parts.append(f"{key}={value:.2f}")
                else:
                    parts.append(f"{key}={value}")
            suffix = " | " + " ".join(parts)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{record['ts']} {level:<6} [{stage}] {message}{suffix}\n")
        return record

    def info(self, stage: str, message: str, **data: Any) -> None:
        self._record("INFO", stage, message, data)

    def warn(self, stage: str, message: str, **data: Any) -> None:
        self._record("WARN", stage, message, data)

    def error(self, stage: str, message: str, **data: Any) -> None:
        self._record("ERROR", stage, message, data)

    def fallback(self, stage: str, reason: str, action: str, **data: Any) -> None:
        payload = {"reason": reason, "fallback_action": action, **data}
        self._record("WARN", stage, f"触发兜底：{reason}", payload)
        self.fallbacks.append({"stage": stage, "reason": reason, "action": action, **data})

    def read_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events
