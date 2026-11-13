"""国产 API / 私有化模型的 OpenAI 兼容客户端。

默认 DeepSeek（国产 API）；设置 LLM_PROVIDER=ollama 即可切换到
本机或内网私有化部署的 Qwen 等模型（OpenAI 兼容端点）。

安全边界：
- 只发送聚合指标与动作摘要，不发送客户名称、合同原文；
- 所有金额和指标由本地代码计算，模型只做目标解析和摘要；
- 摘要中出现的每个数字都必须能在事实包里找到，否则拒绝并降级；
- 密钥不写入日志或运行产物。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import (
    LLM_DEFAULT_BASE_URL,
    LLM_DEFAULT_MODEL,
    LLM_DEFAULT_PROVIDER,
    LLM_DEFAULT_TIMEOUT,
    LLM_MAX_RETRIES,
)
from .errors import LLMError

_PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "openai-compatible": "",
}
_PROVIDER_MODELS = {
    "deepseek": "deepseek-chat",
    "ollama": "qwen2.5:7b-instruct",
    "openai-compatible": "qwen2.5-7b-instruct",
}
_PROVIDER_KEY_ENVS = {
    "deepseek": ["DEEPSEEK_API_KEY", "LLM_API_KEY"],
    "ollama": ["LLM_API_KEY"],
    "openai-compatible": ["LLM_API_KEY", "DASHSCOPE_API_KEY"],
}

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class LLMConfig:
    provider: str
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = LLM_DEFAULT_TIMEOUT
    max_retries: int = LLM_MAX_RETRIES
    enabled: bool = True
    disabled_reason: str = ""
    source: str = "env"

    @classmethod
    def from_env(cls, mode: str = "auto", force_down: bool = False) -> "LLMConfig":
        if mode not in {"auto", "off"}:
            raise LLMError(f"未知 LLM 模式：{mode}")
        provider = os.getenv("LLM_PROVIDER", LLM_DEFAULT_PROVIDER).strip().lower()
        if provider not in _PROVIDER_BASE_URLS:
            provider = "openai-compatible"
        base_url = os.getenv("LLM_BASE_URL") or _PROVIDER_BASE_URLS.get(provider) or LLM_DEFAULT_BASE_URL
        model = os.getenv("LLM_MODEL") or _PROVIDER_MODELS.get(provider, LLM_DEFAULT_MODEL)
        api_key = ""
        for env_name in _PROVIDER_KEY_ENVS.get(provider, ["LLM_API_KEY"]):
            if os.getenv(env_name):
                api_key = os.getenv(env_name, "")
                break
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", str(LLM_DEFAULT_TIMEOUT)))
        max_retries = int(os.getenv("LLM_MAX_RETRIES", str(LLM_MAX_RETRIES)))
        enabled = True
        reason = ""
        if os.getenv("LLM_ENABLED", "").strip().lower() in {"0", "false", "off", "no"}:
            enabled, reason = False, "LLM_ENABLED 被显式关闭"
        elif mode == "off":
            enabled, reason = False, "本次运行使用 --llm off"
        elif provider == "deepseek" and not api_key:
            enabled, reason = False, "未配置 DEEPSEEK_API_KEY / LLM_API_KEY"
        elif provider == "openai-compatible" and not base_url:
            enabled, reason = False, "未配置 LLM_BASE_URL"
        if force_down:
            enabled, reason = False, "chaos: 模拟模型服务不可用"
        return cls(
            provider=provider,
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            enabled=enabled,
            disabled_reason=reason,
            source="chaos" if force_down else "env",
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": bool(self.api_key),
            "timeout_seconds": self.timeout,
            "max_retries": self.max_retries,
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
        }


@dataclass
class LLMClient:
    config: LLMConfig
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.config.enabled

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _chat(self, messages: list[dict[str, str]], purpose: str, json_mode: bool = False) -> str:
        if not self.config.enabled:
            raise LLMError(self.config.disabled_reason or "模型未启用")
        url = f"{self.config.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 800,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 2):
            started = time.time()
            try:
                request = urllib.request.Request(
                    url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=self._headers(),
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    body = response.read().decode("utf-8")
                parsed = json.loads(body)
                content = parsed["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise LLMError("模型返回空内容")
                self.trace.append(
                    {
                        "purpose": purpose,
                        "ok": True,
                        "attempt": attempt,
                        "duration_ms": int((time.time() - started) * 1000),
                        "response_chars": len(content),
                    }
                )
                return content.strip()
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, ValueError, LLMError) as exc:
                last_error = exc
                detail = exc.read().decode("utf-8", errors="replace")[:300] if isinstance(exc, urllib.error.HTTPError) else str(exc)
                self.trace.append(
                    {
                        "purpose": purpose,
                        "ok": False,
                        "attempt": attempt,
                        "duration_ms": int((time.time() - started) * 1000),
                        "error": detail,
                    }
                )
                if attempt <= self.config.max_retries:
                    time.sleep(0.4 * attempt)
        raise LLMError(f"模型调用失败（{purpose}）：{last_error}")

    def parse_goal(self, text: str) -> dict[str, float]:
        system = (
            "你是财务 BP 助手。把老板的口述经营目标解析成两个数字。"
            "只输出 JSON，不要解释。格式："
            '{"margin_delta_pp": 数字, "dso_target_days": 数字}。'
            "margin_delta_pp 是毛利率提升的百分点；dso_target_days 是回款周期目标天数。"
        )
        user = f"老板口述：{text}"
        content = self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            purpose="parse_goal",
            json_mode=True,
        )
        data = _load_json_object(content)
        try:
            return {
                "margin_delta_pp": float(data["margin_delta_pp"]),
                "dso_target_days": float(data["dso_target_days"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError(f"目标解析 JSON 字段不合法：{exc}") from exc

    def narrate(self, facts: dict[str, Any]) -> str:
        system = (
            "你是财务 BP 助手。只根据给定事实写 3 条中文经营摘要，"
            "每条以“- ”开头，不要编号，不要出现客户名称，"
            "不要写任何事实包里没有的数字，不要编造原因。"
        )
        user = "事实包：\n" + json.dumps(facts, ensure_ascii=False, indent=2)
        content = self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            purpose="narrate",
            json_mode=False,
        )
        ok, bad = validate_numbers(content, facts)
        if not ok:
            raise LLMError(f"摘要包含事实包之外的数字：{bad}")
        return content

    def check(self) -> dict[str, Any]:
        started = time.time()
        try:
            content = self._chat(
                [{"role": "user", "content": "只回复两个字：可用"}],
                purpose="check",
            )
            return {
                "ok": True,
                "latency_ms": int((time.time() - started) * 1000),
                "reply": content[:80],
                **self.config.describe(),
            }
        except LLMError as exc:
            return {"ok": False, "error": str(exc), **self.config.describe()}


def _load_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise LLMError("模型输出不是 JSON 对象")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise LLMError("模型输出不是 JSON 对象")
    return data


def validate_numbers(text: str, facts: dict[str, Any]) -> tuple[bool, str]:
    allowed_raw = set(_NUMBER_RE.findall(json.dumps(facts, ensure_ascii=False)))
    allowed = {_normalize_number(token) for token in allowed_raw}
    for token in _NUMBER_RE.findall(text):
        if _normalize_number(token) not in allowed:
            return False, token
    return True, ""


def _normalize_number(token: str) -> str:
    try:
        value = float(token)
    except ValueError:
        return token
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")
