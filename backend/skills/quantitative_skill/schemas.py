from typing import Any

from pydantic import Field, field_validator, model_validator

from backend.agent.base_schemas import BaseSkillTask
from backend.skills.base_schemas import BaseSkillEvidence, SkillContext


class QuantitativeSkillTask(BaseSkillTask):
    """Spatial Agent 下发的统计任务，不包含运行时数据对象。"""

    analysis_scope: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id", "objective", "parent_agent_task_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("任务标识、目标和父任务标识不能为空。")
        return value

    @field_validator("skill_name")
    @classmethod
    def validate_skill_name(cls, value: str) -> str:
        if value != "quantitative_skill":
            raise ValueError("任务必须交给 quantitative_skill。")
        return value

    @model_validator(mode="after")
    def validate_input_refs(self) -> "QuantitativeSkillTask":
        identifiers = [ref.data_id for ref in self.input_refs]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("input_refs 中的 data_id 必须唯一。")
        if any(
            not ref.data_id.strip() or not ref.data_path.strip()
            for ref in self.input_refs
        ):
            raise ValueError("输入数据的标识和路径不能为空。")
        for scope in (self.analysis_scope, self.task_context):
            paths = scope.get("dataset_paths", {})
            if not isinstance(paths, dict) or any(
                not isinstance(path, str) or not path.strip() for path in paths.values()
            ):
                raise ValueError("dataset_paths 必须是包含非空文件路径的字典。")
        return self


class QuantitativeSkillEvidence(BaseSkillEvidence):
    """仅向上游返回事实、统计元数据和告警，不携带 GDF。"""

    statistics_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class QuantitativeSkillContext(SkillContext):
    """量化分析 Skill 的运行时上下文。"""

    data_checks: dict[str, Any] = Field(
        default_factory=dict,
        description="数据检查结果，如空值、重复值、有效记录数、CRS 和单位校验结果。",
    )
