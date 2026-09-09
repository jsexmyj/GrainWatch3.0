from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.agent.base_schemas import BaseSkillTask
from backend.infrastructure.tool_manager.base import ToolResult
from backend.skills.base_schemas import BaseSkillEvidence, SkillContext


class SpatialSkillTask(BaseSkillTask):
    analysis_scope: dict[str, Any] = Field(
        default_factory=dict,
        description="空间分析范围，如 bbox、行政区、缓冲距离、目标 CRS",
    )


class SpatialSkillContext(SkillContext):
    """
    空间分析 Skill 的运行时上下文。
    """

    spatial_references: dict[str, Any] = Field(
        default_factory=dict, description="当前任务涉及的空间数据及其空间属性信息"
    )
    current_crs: str | None = Field(
        default=None, description="当前空间数据主要使用的坐标参考系统"
    )


class SpatialSkillEvidence(BaseSkillEvidence):
    """空间分析 Skill 输出证据。"""

    spatial_scope: dict[str, Any] = Field(
        default_factory=dict,
        description="证据覆盖的空间范围，如 bbox、crs、区域名称",
    )
    analysis_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="空间统计摘要信息，如面积总和、对象数量、空间关系计数。",
    )


class ToolStep(BaseModel):
    """顺序计划中的一次原子工具调用。"""

    step_id: str = Field(..., min_length=1, description="计划内唯一的步骤标识。")
    tool_name: str = Field(..., min_length=1, description="已注册工具的名称。")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="工具输入参数；支持 ${steps.<step_id>.data} 引用前序结果。",
    )
    output_key: str | None = Field(
        default=None, description="将本步骤 ToolResult 写入运行时上下文的键。"
    )
    on_failure: Literal["raise", "retry", "warn", "stop", "continue"] = Field(
        default="raise",
        description=(
            "本步骤失败后的处理策略。"
            "raise=立即抛错；retry=可恢复错误进行重试后抛错；"
            "warn=记录告警并继续。"
            "兼容旧值：stop->raise，continue->warn。"
        ),
    )

    @field_validator("on_failure", mode="before")
    @classmethod
    def normalize_on_failure(cls, value: str) -> str:
        legacy_mapping = {
            "stop": "raise",
            "continue": "warn",
        }
        return legacy_mapping.get(value, value)


class ToolPlan(BaseModel):
    """由 Planner 生成、由 Executor 按声明顺序执行的工具调用计划。"""

    plan_id: str = Field(..., min_length=1, description="计划唯一标识。")
    steps: list[ToolStep] = Field(
        ..., min_length=1, description="按执行顺序排列的工具步骤。"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="规划来源和约束信息。"
    )

    @model_validator(mode="after")
    def validate_unique_step_ids(self) -> "ToolPlan":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("ToolPlan 中的 step_id 必须唯一。")
        return self


class ExecutionResult(BaseModel):
    """Executor 返回的执行轨迹与运行时上下文。"""

    success: bool
    results: list[ToolResult] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    failed_step_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
