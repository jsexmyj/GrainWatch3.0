import re
from typing import Any

from backend.infrastructure.tool_manager.base import ToolResult
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.skills.spatial_skill.schemas import ExecutionResult, ToolPlan

_STEP_REFERENCE = re.compile(r"^\$\{steps\.([^.}]+)\.data\}$")


class SpatialExecutor:
    """确定性地执行 ToolPlan，并将每步结果写入运行时上下文。"""

    def __init__(self, tool_manager: ToolManager):
        self.tool_manager = tool_manager

    async def execute(self, plan: ToolPlan) -> ExecutionResult:
        context: dict[str, Any] = {}
        results: list[ToolResult] = []

        for step in plan.steps:
            # 先把参数中的占位符替换成上一步真实输出，再执行当前工具。
            arguments = self._resolve_references(step.arguments, context)
            result = await self.tool_manager.execute(step.tool_name, **arguments)
            results.append(result)
            # 同时保留 step_id 与 output_key 两种索引，便于后续引用。
            context[step.step_id] = result
            if step.output_key:
                context[step.output_key] = result

            if not result.success and step.on_failure == "stop":
                return ExecutionResult(
                    success=False,
                    results=results,
                    context=context,
                    failed_step_id=step.step_id,
                )

        return ExecutionResult(
            success=all(result.success for result in results),
            results=results,
            context=context,
        )

    def _resolve_references(self, value: Any, context: dict[str, Any]) -> Any:
        """核心是把字符串中的占位符替换为对应的结果值。"""
        if isinstance(value, str):
            matched = _STEP_REFERENCE.fullmatch(value)
            if not matched:
                return value
            step_id = matched.group(1)
            result = context.get(step_id)
            if not isinstance(result, ToolResult):
                raise ValueError(f"无法解析步骤结果引用: {value}")
            return result.data
        if isinstance(value, dict):
            return {
                key: self._resolve_references(item, context)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_references(item, context) for item in value]
        return value
