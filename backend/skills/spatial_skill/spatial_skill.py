from pathlib import Path
from typing import Any

from backend.agent.base_schemas import DataRef
from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.tool_manager.loader import ToolLoader
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.infrastructure.tool_manager.base import ToolResult
from backend.skills.spatial_skill.executor import SpatialExecutor
from backend.skills.spatial_skill.planner import SpatialSkillPlanner
from backend.skills.spatial_skill.schemas import (
    ExecutionResult,
    SpatialSkillEvidence,
    SpatialSkillTask,
)
from backend.utils.logger import get_logger
from backend.utils.paths import PATHS

# 接收 SpatialTask
# 调用 Planner 生成 ToolPlan
# 调用 Executor 执行 ToolPlan
# ToolResult 转换成 Evidence

logger = get_logger(__name__)


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
        evidence = self.build_evidence(task, execution)

        return evidence

    @staticmethod
    def build_evidence(
        task: SpatialSkillTask, execution: ExecutionResult
    ) -> SpatialSkillEvidence:
        successful_results = [result for result in execution.results if result.success]
        # final_result = successful_results[-1] if successful_results else None
        facts: list[str] = []
        for result in successful_results:
            fact = result.fact
            if fact:
                facts.append(fact)

        output_refs = SpatialSkill._build_output_refs(task, execution.results)
        operations = [
            SpatialSkill._build_operation_item(result) for result in successful_results
        ]

        if execution.success:
            status = "success"
        elif successful_results:
            status = "partial"
        else:
            status = "failed"

        # 仅输出结构化证据，不直接暴露大型中间对象。
        evidence = SpatialSkillEvidence(
            task_id=task.task_id,
            source_skill="spatial_skill",
            status=status,
            summary=(
                "".join(facts) if facts else "本次空间流程未产出可供复用的关键事实。"
            ),
            facts=facts,
            output_refs=output_refs,
            error=(
                None if execution.success else f"failed_step={execution.failed_step_id}"
            ),
            spatial_scope={
                "analysis_scope": task.analysis_scope,
                "crs": task.analysis_scope.get("crs"),
            },
            operation_summary={
                "operations": operations,
            },
        )
        logger.info(
            f"spatial skill最终生成空间证据：{evidence.model_dump_json(indent=2)}"
        )
        return evidence

    @staticmethod
    def _build_output_refs(
        task: SpatialSkillTask, results: list[ToolResult]
    ) -> list[DataRef]:
        output_refs: list[DataRef] = []

        for index, result in enumerate(results, start=1):
            if not result.success or result.result_type != "file":
                continue

            data_ref = SpatialSkill._result_to_data_ref(task, result, index)
            if data_ref is not None:
                output_refs.append(data_ref)

        return output_refs

    @staticmethod
    def _result_to_data_ref(
        task: SpatialSkillTask,
        result: ToolResult,
        index: int,
    ) -> DataRef | None:
        file_path = ""
        file_name = ""
        file_metadata: dict[str, Any] = {}

        if isinstance(result.data, FileResource):
            file_path = result.data.path
            file_name = result.data.file_name
            file_metadata = {
                **result.data.metadata,
                "format": result.data.format,
                "size_bytes": result.data.size_bytes,
            }
        elif isinstance(result.data, dict):
            file_path = str(
                result.data.get("path") or result.data.get("file_path") or ""
            )
            file_name = str(result.data.get("file_name") or "")

            metadata_value = result.data.get("metadata")
            if isinstance(metadata_value, dict):
                file_metadata.update(metadata_value)

            if "format" in result.data:
                file_metadata["format"] = result.data["format"]
            if "size_bytes" in result.data:
                file_metadata["size_bytes"] = result.data["size_bytes"]
        else:
            return None

        if not file_path:
            return None

        normalized_path = SpatialSkill._to_project_relative_path(file_path)
        if not file_name:
            file_name = Path(normalized_path).name

        return DataRef(
            data_id=f"{task.task_id}:{result.tool_name}:{index}",
            data_type="file",
            data_path=normalized_path,
            data_name=file_name,
            metadata={
                "tool_name": result.tool_name,
                **result.metadata,
                **file_metadata,
            },
        )

    @staticmethod
    def _to_project_relative_path(path_value: str) -> str:
        path = Path(path_value)
        if not path.is_absolute():
            path = (PATHS.root / path).resolve()

        try:
            return path.relative_to(PATHS.root).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _extract_file_name(data: Any) -> str | None:
        if isinstance(data, FileResource):
            return data.file_name
        if isinstance(data, dict):
            if data.get("file_name"):
                return str(data["file_name"])
            if data.get("path"):
                return Path(str(data["path"])).name
        return None

    @staticmethod
    def _build_operation_item(result: ToolResult) -> dict[str, Any]:
        operation: dict[str, Any] = {"operation": result.tool_name}
        operation.update(result.metadata or {})
        return operation
