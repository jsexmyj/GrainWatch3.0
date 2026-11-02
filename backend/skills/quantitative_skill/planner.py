from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.agent.base_schemas import BaseSkillTask
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.skills.base_schemas import ToolPlan
from backend.skills.planner_llm_strategy import PlanLLMConfig, PlanLLMStrategy
from backend.skills.quantitative_skill.quantitative_prompt import (
    build_quantitative_planning_prompt,
)
from backend.skills.quantitative_skill.references import (
    iter_references,
    plan_dependencies,
)
from backend.utils.logger import get_logger
from backend.utils.paths import PATHS

logger = get_logger(__name__)


class QuantitativeSkillPlanner:
    """仅规划统计任务，工具加载及数据准备由外部组件负责。"""

    def __init__(
        self,
        registry: ToolRegistry,
        llm_strategy: PlanLLMStrategy,
        tool_directory: (
            str | Path | list[str | Path] | tuple[str | Path, ...] | None
        ) = None,
        llm_config: PlanLLMConfig | None = None,
    ):
        self.registry = registry
        self.llm_strategy = llm_strategy
        self.tool_directory = tool_directory or PATHS.infrastructure_path(
            "gis", "statistics"
        )
        self.llm_config = llm_config or llm_strategy.config

    async def plan(self, task: BaseSkillTask) -> ToolPlan:
        catalog = deepcopy(self.registry.list_tool_catalog(self.tool_directory))
        if task.allowed_tools:
            catalog = {
                name: details
                for name, details in catalog.items()
                if name in task.allowed_tools
            }
        if not catalog:
            raise ValueError("当前任务没有可用的统计工具。")

        # 注册表描述的是运行时 GDF，规划阶段用路径或引用表达该输入。
        for details in catalog.values():
            properties = details["input_schema"].get("properties", {})
            for name in ("gdf", "source"):
                if name in properties:
                    properties[name] = {
                        "type": "string",
                        "description": "输入文件路径、${inputs.<data_id>} 或 ${steps.<step_id>.data}。",
                    }

        logger.info(
            "开始量化规划 task_id=%s, available_tools=%d", task.task_id, len(catalog)
        )
        payload = await self.llm_strategy.generate_plan_payload(
            prompt=build_quantitative_planning_prompt(
                task, catalog, self.llm_config.mode
            ),
            plan_schema=ToolPlan.model_json_schema(),
        )
        plan = ToolPlan.model_validate(self._normalize_tool_calling_payload(payload))
        inputs = {ref.data_id for ref in task.input_refs}
        for step in plan.steps:
            if step.tool_name not in catalog:
                raise ValueError(f"ToolPlan 包含不可用的统计工具：{step.tool_name}")
            if step.on_failure == "retry":
                raise ValueError("量化统计暂不支持 retry 策略。")
            fields = self.registry.get_tool(step.tool_name).input_model.model_fields
            missing = {
                name for name, field in fields.items() if field.is_required()
            } - step.arguments.keys()
            unknown = step.arguments.keys() - fields.keys()
            if missing or unknown:
                raise ValueError(
                    f"步骤 {step.step_id} 的参数不合法：missing={sorted(missing)}, unknown={sorted(unknown)}"
                )
            for kind, identifier in iter_references(step.arguments):
                if kind == "inputs" and identifier not in inputs:
                    raise ValueError(
                        f"步骤 {step.step_id} 引用了不存在的输入：{identifier}"
                    )

        dependencies = plan_dependencies(plan)
        plan.metadata.update(task_id=task.task_id, execution_mode="dependency_parallel")
        logger.info(
            "量化规划完成 plan_id=%s, steps=%d, dependent_steps=%d",
            plan.plan_id,
            len(plan.steps),
            sum(bool(value) for value in dependencies.values()),
        )
        logger.debug("量化工具计划：%s", plan.model_dump_json(indent=2))
        return plan

    @staticmethod
    def _normalize_tool_calling_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if "plan_id" in payload and "steps" in payload:
            return payload
        arguments = payload.get("arguments")
        if (
            isinstance(arguments, dict)
            and "plan_id" in arguments
            and "steps" in arguments
        ):
            return arguments
        raise ValueError("LLM 未返回可解析的 ToolPlan 结构。")
