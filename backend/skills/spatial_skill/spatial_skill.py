from backend.infrastructure.tool_manager.loader import ToolLoader
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.skills.spatial_skill.executor import SpatialExecutor
from backend.skills.spatial_skill.planner import SpatialSkillPlanner
from backend.skills.spatial_skill.schemas import (
    ExecutionResult,
    SpatialSkillEvidence,
    SpatialSkillTask,
)
from backend.utils.paths import PATHS

# 接收 SpatialTask
# 调用 Planner 生成 ToolPlan
# 调用 Executor 执行 ToolPlan
# ToolResult 转换成 Evidence


class SpatialSkill:

    def __init__(
        self,
        planner: SpatialSkillPlanner,
        executor: SpatialExecutor | None = None,
        registry: ToolRegistry | None = None,
        load_tools: bool = True,
    ):
        self.registry = registry or ToolRegistry()
        if load_tools:
            ToolLoader.auto_load_tools(
                registry=self.registry,
                package_path=PATHS.infrastructure_path("gis", "raster"),
            )
            ToolLoader.auto_load_tools(
                registry=self.registry,
                package_path=PATHS.infrastructure_path("gis", "vector"),
            )
        self.tool_manager = ToolManager(self.registry)
        self.planner = planner
        self.executor = executor or SpatialExecutor(self.tool_manager)

    async def execute(self, task: SpatialSkillTask) -> SpatialSkillEvidence:

        # 1.规划
        plan = await self.planner.plan(
            task,
        )

        # 2.执行（执行器负责引用解析与失败策略）
        execution = await self.executor.execute(plan)

        # 3.组织Evidence
        evidence = self.build_evidence(task, plan.plan_id, execution)

        return evidence

    @staticmethod
    def build_evidence(
        task: SpatialSkillTask, plan_id: str, execution: ExecutionResult
    ) -> SpatialSkillEvidence:
        successful_results = [result for result in execution.results if result.success]
        final_result = successful_results[-1] if successful_results else None
        tool_names = [result.tool_name for result in execution.results]
        confidence = (
            len(successful_results) / len(execution.results)
            if execution.results
            else 0.0
        )
        # 仅输出结构化证据，不直接暴露大型中间对象。
        return SpatialSkillEvidence(
            task_id=task.task_id,
            source_skill="spatial_skill",
            status="success" if execution.success else "partial",
            summary=f"已执行空间工具: {' -> '.join(tool_names) or '无'}。",
            facts=[
                f"plan_id={plan_id}",
                f"steps={len(execution.results)}",
                f"success_rate={confidence:.2f}",
            ],
            output_refs=[],
            error=(
                None if execution.success else f"failed_step={execution.failed_step_id}"
            ),
            spatial_scope={
                "analysis_scope": task.analysis_scope,
                "crs": task.analysis_scope.get("crs"),
            },
            analysis_summary={
                "plan_id": plan_id,
                "success": execution.success,
                "failed_step_id": execution.failed_step_id,
                "final_metadata": final_result.metadata if final_result else {},
            },
        )
