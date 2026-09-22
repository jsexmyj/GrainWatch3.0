from pathlib import Path
from typing import Any

from backend.agent.base_schemas import BaseSkillTask
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.skills.planner_llm_strategy import PlanLLMConfig, PlanLLMStrategy
from backend.skills.base_schemas import ToolPlan
from backend.skills.spatial_skill.spatial_prompt import build_spatial_planning_prompt
from backend.utils.logger import get_logger
from backend.utils.paths import PATHS

logger = get_logger(__name__)


class SpatialSkillPlanner:
    """将空间任务转换为受注册工具约束的顺序 ToolPlan。"""

    def __init__(
        self,
        registry: ToolRegistry,
        llm_strategy: PlanLLMStrategy,
        tool_directory: (
            str | Path | list[str | Path] | tuple[str | Path, ...] | None
        ) = None,
        # tool_directory 不负责加载工具，而是负责限制 LLM 规划时可见的工具范围。
        llm_config: PlanLLMConfig | None = None,
    ):
        self.registry = registry
        self.llm_strategy = llm_strategy
        # 这个tool directory类似于一个过滤器，只能看到这个目录下的工具，
        # 但是执行调用的时候registry可能有其他工具加载，
        # 所以要注意保持registry和tool_directory一致
        self.tool_directory = tool_directory or (
            PATHS.infrastructure_path("gis", "vector"),
            PATHS.infrastructure_path("gis", "raster"),
        )
        self.llm_config = llm_config or llm_strategy.config

    async def plan(self, task: BaseSkillTask) -> ToolPlan:
        tool_catalog = self.registry.list_tool_catalog(self.tool_directory)
        if task.allowed_tools:
            tool_catalog = {
                name: details
                for name, details in tool_catalog.items()
                if name in task.allowed_tools
            }
        prompt = build_spatial_planning_prompt(
            task=task,
            tool_catalog=tool_catalog,
            output_mode=self.llm_config.mode,
        )

        # 以 ToolPlan JSON Schema 作为强约束，给 tool-calling 或 JSON 模式共用。
        payload = await self.llm_strategy.generate_plan_payload(
            prompt=prompt,
            plan_schema=ToolPlan.model_json_schema(),
        )
        payload = self._normalize_tool_calling_payload(payload)
        plan = ToolPlan.model_validate(payload)
        logger.debug("空间skill输出计划:\n%s", plan.model_dump_json(indent=2))
        available_tools = set(tool_catalog)
        unknown_tools = [
            step.tool_name
            for step in plan.steps
            if step.tool_name not in available_tools
        ]
        if unknown_tools:
            raise ValueError(f"ToolPlan 包含未注册工具: {', '.join(unknown_tools)}")
        return plan

    @staticmethod
    def _normalize_tool_calling_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """
        兼容两类返回:
        1) 直接返回 ToolPlan dict
        2) tool calling 样式：{"tool_name": "create_tool_plan", "arguments": {...}}
        """
        if "plan_id" in payload and "steps" in payload:
            return payload
        if "arguments" in payload and isinstance(payload["arguments"], dict):
            args = payload["arguments"]
            if "plan_id" in args and "steps" in args:
                return args
        raise ValueError("LLM 未返回可解析的 ToolPlan 结构。")
