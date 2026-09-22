from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from backend.agent.base_schemas import BaseTask, DataRef


class SkillExecutionResult(BaseModel):
    """
    单次工具执行产生的结构化结果。

    该结果用于支持后续步骤的数据引用和 Skill 最终证据构建，
    不包含大规模实际数据。
    """

    step_id: str = Field(description="当前执行步骤的唯一标识")
    tool_name: str = Field(description="实际执行的工具名称")
    status: str = Field(description="工具执行状态，例如 success 或 failed")
    output_refs: list[str] = Field(
        default_factory=list,
        description="工具产生的数据资源引用，例如文件路径或资源 URI",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="工具执行产生的结构化元数据，例如数据类型、坐标系、数量或空间范围",
    )
    error: str | None = Field(default=None, description="工具执行失败时的错误信息")


class SkillContext(BaseModel):
    """
    Skill 执行期间维护的运行时上下文。

    Context 用于保存执行状态和中间结果引用，
    不直接保存大规模空间数据或其他运行时对象。
    """

    task_id: str = Field(description="当前 Skill 所属任务的唯一标识")
    task: BaseTask = Field(description="当前 Skill 正在执行的任务")
    artifacts_refs: dict[str, str] = Field(
        default_factory=dict,
        description="任务过程中产生的数据资源引用，键为资源标识，值为资源路径或资源 URI",
    )
    execution_results: list["SkillExecutionResult"] = Field(
        default_factory=list, description="当前 Skill 已执行步骤的结果记录"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="当前 Skill 执行过程中产生的辅助元数据"
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


class BaseSkillEvidence(BaseModel):
    """
    Skill 执行后形成的专业事实证据。
    """

    task_id: str = Field(description="当前 Skill 所属任务的唯一标识")
    source_skill: str = Field(description="产生该证据的 Skill 名称")
    status: Literal["success", "partial", "failed"] = Field(
        description="Skill 执行状态"
    )

    summary: str = Field(description="当前 Skill 对执行结果的简要概括")
    facts: list[str] = Field(
        default_factory=list, description="当前 Skill 产生的关键事实"
    )
    output_refs: list[DataRef] = Field(
        default_factory=list,
        description="Skill 产生的可复用结果引用，例如 result layer、统计表文件",
    )
    error: str | None = Field(default=None, description="工具执行失败时的错误信息")
