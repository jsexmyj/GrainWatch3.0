from typing import Any

from backend.infrastructure.llm.siliconflow_client import SiliconFlowLLMClient
from backend.skills.planner_llm_strategy import PlanLLMConfig, PlanLLMStrategy


def create_siliconflow_plan_strategy(
    config: PlanLLMConfig,
    service: SiliconFlowLLMClient | None = None,
) -> PlanLLMStrategy:
    """把通用规划策略绑定到 SiliconFlow，保持 Planner 与供应商解耦。"""
    llm_service = service or SiliconFlowLLMClient()

    async def invoke_fn(prompt: str, request: dict[str, Any]) -> str | dict[str, Any]:
        return await llm_service.unified_invoke(
            prompt=prompt,
            model=request["model"],
            mode=request["mode"],
            temperature=request["temperature"],
            max_tokens=request["max_tokens"],
            tool_name=request["tool_name"],
            response_schema=request["response_format_schema"],
            json_mode_supported=request["json_mode_supported"],
        )

    return PlanLLMStrategy(config=config, invoke_fn=invoke_fn)
