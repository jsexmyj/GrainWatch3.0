"""空间子智能体的委托任务、技能计划、私有状态与返回证据。"""

from datetime import date, datetime
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from backend.agent.base_schemas import BaseAgentEvidence, BaseAgentTask, DataRef


class ResearchPeriod(BaseModel):
    """空间分析所覆盖的数据时间范围，允许仅指定一端。"""

    start: date | None = Field(default=None, description="研究时段的起始日期，含当天。")
    end: date | None = Field(default=None, description="研究时段的结束日期，含当天。")

    @model_validator(mode="after")
    def validate_dates(self) -> "ResearchPeriod":
        if self.start and self.end and self.start > self.end:
            raise ValueError("研究起始日期不能晚于结束日期。")
        return self


class SpatialAgentTask(BaseAgentTask):
    """主智能体委托给空间子智能体的分析任务。"""

    agent_name: Literal["spatial_agent"] = Field(
        default="spatial_agent", description="接收本次委托的空间子智能体名称。"
    )
    analysis_scope: dict[str, Any] = Field(
        default_factory=dict, description="研究区域，例如行政区名称、边界框及其坐标系。"
    )
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="分析和数据使用约束，例如允许的数据源、分辨率和精度要求。",
    )
    expected_outputs: list[str] = Field(
        default_factory=list,
        description="主智能体期望收到的结果，例如相交图层或分区面积统计表。",
    )
    research_period: ResearchPeriod | None = Field(
        default=None, description="输入数据和分析结论应覆盖的研究时段。"
    )


class SkillStep(BaseModel):
    """计划中的一次技能调用。"""

    step_id: str = Field(min_length=1, description="计划内唯一的调用步骤标识。")
    skill_name: str = Field(min_length=1, description="本步骤要调用的技能名称。")
    objective: str = Field(description="本次技能调用要完成的具体目标。")
    depends_on: list[str] = Field(
        default_factory=list,
        description="本步骤依赖的前置步骤标识，空列表表示可独立执行。",
    )


class SkillPlan(BaseModel):
    """空间子智能体为一次委托制定的技能调用计划。"""

    steps: list[SkillStep] = Field(
        min_length=1, description="完成任务所需的技能调用及其依赖关系。"
    )

    @model_validator(mode="after")
    def validate_dependencies(self) -> "SkillPlan":
        dependencies = {step.step_id: step.depends_on for step in self.steps}
        if len(dependencies) != len(self.steps):
            raise ValueError("计划中的步骤标识必须唯一。")
        for step_id, parents in dependencies.items():
            unknown = set(parents).difference(dependencies)
            if unknown:
                raise ValueError(
                    f"步骤 {step_id} 依赖未知步骤：{', '.join(sorted(unknown))}"
                )
        try:
            TopologicalSorter(dependencies).prepare()
        except CycleError as exc:
            raise ValueError("技能调用步骤之间存在循环依赖。") from exc
        return self


class SkillExecutionState(BaseModel):
    """一次技能调用在子智能体内的执行记录。"""

    status: Literal["pending", "running", "success", "partial", "failed", "skipped"] = (
        Field(
            default="pending",
            description="该步骤的状态，依次可取待执行、执行中、成功、部分成功、失败或跳过。",
        )
    )
    errors: list[str] = Field(
        default_factory=list, description="按发生顺序记录该步骤历次执行的错误信息。"
    )
    output_refs: list[DataRef] = Field(
        default_factory=list, description="该步骤已生成、可供后续步骤使用的数据引用。"
    )


class RecoveryRecord(BaseModel):
    """针对一个技能步骤的局部恢复记录。"""

    step_id: str = Field(description="需要局部恢复的技能调用步骤标识。")
    reason: str = Field(description="触发本次恢复的失败原因。")
    action: str = Field(description="实际采取的恢复措施，例如更换输入图层后重试。")
    outcome: str = Field(description="本次恢复的结果，包括是否可以继续执行及遗留问题。")


class SpatialAgentState(BaseModel):
    """由空间子智能体维护的私有执行状态。"""

    task: SpatialAgentTask = Field(description="本次执行对应的主智能体委托任务。")
    skill_plan: SkillPlan | None = Field(
        default=None, description="当前技能调用计划，尚未规划时为空。"
    )
    step_states: dict[str, SkillExecutionState] = Field(
        default_factory=dict,
        description="各技能调用的执行记录，以计划中的步骤标识为键。",
    )
    recovery_history: list[RecoveryRecord] = Field(
        default_factory=list, description="按发生顺序保存的局部恢复记录。"
    )


class SpatialAgentEvidence(BaseAgentEvidence):
    """空间子智能体汇总技能执行结果后返回主智能体的证据。"""

    source_agent: Literal["spatial_agent"] = Field(
        default="spatial_agent", description="生成证据的空间子智能体名称。"
    )
    status: Literal["success", "partial", "failed"] = Field(
        description="整体执行结果，分别表示成功、部分成功或失败。"
    )
    spatial_scope: dict[str, Any] = Field(
        default_factory=dict, description="证据实际覆盖的空间范围及坐标系。"
    )
    operation_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="产生空间事实的操作及参数，例如缓冲距离和相交条件。",
    )
    statistics_summary: dict[str, Any] = Field(
        default_factory=dict, description="量化分析得到的统计指标、数值及单位。"
    )
    output_refs: list[DataRef] = Field(
        default_factory=list,
        description="可供主智能体复用的结果图层、栅格或统计表引用。",
    )
    warnings: list[str] = Field(
        default_factory=list, description="影响结果解释或适用范围的告警。"
    )
    errors: list[str] = Field(
        default_factory=list, description="导致任务失败或仅部分完成的错误信息。"
    )
