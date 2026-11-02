from pathlib import Path
from typing import Any

from backend.agent.base_schemas import BaseSkillTask, DataRef
from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.tool_manager.base import ToolResult
from backend.infrastructure.tool_manager.loader import ToolLoader
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.skills.base_schemas import ExecutionResult
from backend.skills.quantitative_skill.excutor import QuantitativeExecutor
from backend.skills.quantitative_skill.planner import QuantitativeSkillPlanner
from backend.skills.quantitative_skill.schemas import (
    QuantitativeSkillEvidence,
    QuantitativeSkillTask,
)
from backend.skills.quantitative_skill.validation import QuantitativeValidator
from backend.utils.logger import get_logger
from backend.utils.paths import PATHS

logger = get_logger(__name__)


class QuantitativeSkill:
    """任务验证 -> 文件验证 -> 规划 -> 工具数据验证 -> 执行 -> 证据。"""

    def __init__(
        self,
        planner: QuantitativeSkillPlanner,
        executor: QuantitativeExecutor | None = None,
        registry: ToolRegistry | None = None,
        load_tools: bool = True,
        validator: QuantitativeValidator | None = None,
    ):
        self.registry = registry if registry is not None else planner.registry
        if self.registry is not planner.registry:
            raise ValueError("Skill 和 Planner 必须使用同一个 ToolRegistry。")
        if executor is not None and executor.tool_manager.registry is not self.registry:
            raise ValueError("Skill 和 Executor 必须使用同一个 ToolRegistry。")
        if load_tools:
            ToolLoader.auto_load_tools(
                self.registry, PATHS.infrastructure_path("gis", "statistics")
            )
        self.tool_manager = ToolManager(self.registry)
        self.planner = planner
        self.validator = validator or (
            executor.validator if executor is not None else QuantitativeValidator()
        )
        self.executor = executor or QuantitativeExecutor(
            self.tool_manager, validator=self.validator
        )

    async def execute(
        self, task: QuantitativeSkillTask | BaseSkillTask | dict[str, Any]
    ) -> QuantitativeSkillEvidence:
        # 重新验证模型，避免 model_construct 或模型字段后续赋值绕过入口检查。
        payload = task.model_dump() if isinstance(task, BaseSkillTask) else task
        task = QuantitativeSkillTask.model_validate(payload)
        logger.info(
            "开始量化任务 task_id=%s, inputs=%d", task.task_id, len(task.input_refs)
        )
        for ref in task.input_refs:
            self.validator.validate_file(ref.data_path)
        for scope in (task.analysis_scope, task.task_context):
            for path in scope.get("dataset_paths", {}).values():
                self.validator.validate_file(path)

        plan = await self.planner.plan(task)
        prepared = await self.executor.prepare(plan, task.input_refs)
        execution = await self.executor.execute(prepared)
        evidence = self.build_evidence(task, execution)
        logger.info(
            "量化任务完成 task_id=%s, status=%s, facts=%d, warnings=%d",
            task.task_id,
            evidence.status,
            len(evidence.facts),
            len(evidence.warnings),
        )
        return evidence

    @staticmethod
    def build_evidence(
        task: QuantitativeSkillTask, execution: ExecutionResult
    ) -> QuantitativeSkillEvidence:
        successful = [result for result in execution.results if result.success]
        facts = [result.fact for result in successful if result.fact]
        status = (
            "success" if execution.success else ("partial" if successful else "failed")
        )
        failures = [
            result.error or f"工具 {result.tool_name} 失败"
            for result in execution.results
            if not result.success
        ]
        output_refs: list[DataRef] = []
        for index, result in enumerate(execution.results, start=1):
            if result.success and result.result_type == "file":
                reference = QuantitativeSkill._file_reference(task, result, index)
                if reference:
                    output_refs.append(reference)
        evidence =  QuantitativeSkillEvidence(
            task_id=task.task_id,
            source_skill="quantitative_skill",
            status=status,
            summary=(
                "；".join(facts) if facts else "本次量化流程未产出可复用的统计事实。"
            ),
            facts=facts,
            output_refs=output_refs,
            error="；".join(failures) if failures else None,
            warnings=execution.warnings,
            statistics_summary={
                "operations": [
                    {
                        "operation": result.tool_name,
                        "result_type": result.result_type,
                        **result.metadata,
                    }
                    for result in successful
                ],
            },
        )
        logger.debug("定量统计skill返回的evidence：%s",evidence.model_dump_json(indent=2))
        return evidence

    @staticmethod
    def _file_reference(
        task: QuantitativeSkillTask, result: ToolResult, index: int
    ) -> DataRef | None:
        if isinstance(result.data, FileResource):
            resource = result.data.model_dump()
        elif isinstance(result.data, dict):
            resource = result.data
        else:
            return None
        path_value = resource.get("path") or resource.get("file_path")
        if not path_value:
            return None
        path = PATHS.join(PATHS.root, str(path_value))
        try:
            normalized = path.relative_to(PATHS.root).as_posix()
        except ValueError:
            normalized = path.as_posix()
        return DataRef(
            data_id=f"{task.task_id}:{result.tool_name}:{index}",
            data_type="file",
            data_path=normalized,
            data_name=resource.get("file_name") or Path(normalized).name,
            metadata={
                "tool_name": result.tool_name,
                **result.metadata,
                **resource.get("metadata", {}),
                **{
                    key: resource[key]
                    for key in ("format", "size_bytes")
                    if key in resource
                },
            },
        )
