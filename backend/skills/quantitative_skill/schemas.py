from typing import Any

from pydantic import Field

from backend.agent.base_schemas import BaseSkillTask
from backend.skills.base_schemas import SkillContext


class QuantitativeSkillContext(SkillContext):
    """量化分析 Skill 的运行时上下文。"""
    data_checks: dict[str, Any] = Field(
        default_factory=dict,
        description="数据检查结果，如空值、重复值、有效记录数、CRS 和单位校验结果。",
    )
