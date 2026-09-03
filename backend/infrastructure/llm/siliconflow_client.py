import json
import os
import time
from typing import Any, Literal
from openai import AsyncOpenAI

from backend.utils.logger import get_logger

logger = get_logger("SiliconFlowLLMService")


class SiliconFlowLLMClient:
    """通用的硅基流动（或任何 OpenAI 兼容渠道）大模型服务。"""

    def __init__(self):
        # 统一从环境变量中读取
        self.api_key = os.getenv("SILICONFLOW_API_KEY", "")
        self.base_url = os.getenv(
            "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
        )

        if not self.api_key:
            raise ValueError("未配置 SILICONFLOW_API_KEY 环境变量")

        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def unified_invoke(
        self,
        prompt: str,
        model: str,
        mode: Literal["json", "tool_calling"] = "tool_calling",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tool_name: str = "execute_plan",
        response_schema: dict[str, Any] | None = None,
        json_mode_supported: bool = False,
    ) -> str | dict[str, Any]:
        """
        一个通用的大模型调用方法，支持JSON 模式和 Tool Calling。
        """
        messages = [{"role": "user", "content": prompt}]
        started_at = time.perf_counter()

        # 1. 如果是 Tool Calling 模式
        if mode == "tool_calling" and response_schema:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": "生成任务规划的执行步骤",
                        "parameters": response_schema,
                    },
                }
            ]

            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore
                tools=tools,  # type: ignore
                # 强迫模型必须调用这个工具，保证输出格式
                tool_choice={"type": "function", "function": {"name": tool_name}},
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # 解析并返回 Tool Calling 的参数部分
            tool_calls = response.choices[0].message.tool_calls or []
            if not tool_calls:
                raise ValueError("LLM 未返回规划工具调用。")
            tool_call = tool_calls[0]
            result = json.loads(tool_call.function.arguments)
            self._log_usage(model, mode, started_at, response)
            return result

        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if mode == "json" and json_mode_supported:
            request["response_format"] = {"type": "json_object"}

        response = await self.client.chat.completions.create(**request)
        self._log_usage(model, mode, started_at, response)
        return response.choices[0].message.content or ""

    @staticmethod
    def _log_usage(model: str, mode: str, started_at: float, response: Any) -> None:
        usage = getattr(response, "usage", None)
        logger.info(
            "llm_request_completed provider=siliconflow model_version=%s mode=%s elapsed_ms=%.2f prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            model,
            mode,
            (time.perf_counter() - started_at) * 1000,
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(usage, "total_tokens", None),
        )
