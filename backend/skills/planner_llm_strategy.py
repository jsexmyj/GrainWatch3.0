import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.utils.logger import get_logger

logger = get_logger(__name__)


class PlanLLMConfig(BaseModel):
    """Skill 级通用的大模型规划配置。"""

    provider: str = Field(default="custom", description="模型提供方标识")
    model: str = Field(default="", description="模型名称")
    mode: Literal["json", "tool_calling"] = Field(
        default="tool_calling", description="规划输出模式"
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    json_mode_supported: bool = Field(
        default=False,
        description="模型是否支持 OpenAI 兼容的 response_format=json_object",
    )
    tool_name: str = Field(
        default="create_tool_plan", description="tool calling 模式下的函数名"
    )


class PlanLLMStrategy:
    """
    统一封装 Skill 的计划生成入口。

    为了兼容不同框架，invoke_fn 接受 prompt 与结构化参数，
    返回 str 或 dict 即可。
    """

    def __init__(
        self,
        config: PlanLLMConfig,
        invoke_fn: Callable[
            [str, dict[str, Any]],
            Awaitable[str | dict[str, Any]] | str | dict[str, Any],
        ],
    ):
        self.config = config
        self.invoke_fn = invoke_fn

    async def generate_plan_payload(
        self,
        prompt: str,
        plan_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """把提示词和配置组装成一个标准的字典，让invoke_fn处理，再讲处理结果统一解析为python字典"""
        request = {
            "mode": self.config.mode,
            "provider": self.config.provider,
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "json_mode_supported": self.config.json_mode_supported,
            "tool_name": self.config.tool_name,
            "response_format_schema": plan_schema,
        }
        started_at = time.perf_counter()
        output = self.invoke_fn(prompt, request)

        if hasattr(output, "__await__"):
            output = await output  # type: ignore[assignment]

        if isinstance(output, dict):
            logger.info(
                "llm_plan_generated model_version=%s mode=%s elapsed_ms=%.2f output_type=dict",
                self.config.model,
                self.config.mode,
                (time.perf_counter() - started_at) * 1000,
            )
            return output
        if isinstance(output, str):
            payload = self._parse_json_output(output)
            logger.info(
                "llm_plan_generated provider=%s model_version=%s mode=%s elapsed_ms=%.2f output_type=text",
                self.config.provider,
                self.config.model,
                self.config.mode,
                (time.perf_counter() - started_at) * 1000,
            )
            return payload
        raise TypeError("LLM 规划输出必须是 dict 或 JSON 字符串。")

    @staticmethod
    def _parse_json_output(output: str) -> dict[str, Any]:
        """解析原生 JSON、Markdown JSON 或普通对话中的首个 JSON 对象。"""
        cleaned = output.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
        if fenced:
            cleaned = fenced.group(1)

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            start = cleaned.find("{")
            if start < 0:
                raise ValueError("LLM 输出中未找到 JSON 对象。") from None
            try:
                payload, _ = decoder.raw_decode(cleaned[start:])
            except json.JSONDecodeError as exc:
                raise ValueError("LLM 输出不是可解析的 JSON 对象。") from exc

        if not isinstance(payload, dict):
            raise ValueError("LLM JSON 输出必须是对象。")
        return payload
