from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from backend.agent.base_schemas import BaseSkillTask
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
    spatial_scope: dict[str, Any] = Field(
        default_factory=dict,
        description="证据覆盖的空间范围，如 bbox、crs、区域名称",
    )
