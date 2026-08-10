from typing import Any, Literal

from pydantic import BaseModel, Field


class DataRef(BaseModel):
    """Agent 与 Skill 之间传递的抽象输入数据引用。"""

    data_id: str = Field(
        ..., description="数据的稳定标识，如图层 ID、文件 ID 或结果 ID。"
    )
    data_type: str = Field(
        ..., description="数据类型，如 vector、raster、table、feature_collection。"
    )
    data_path: str = Field(description="数据的相对路径")
    data_name: str = Field(description="数据的名称")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="坐标系、字段说明、时间范围等附加信息。"
    )


class BaseTask(BaseModel):
    """
    Agent 或 Skill 执行任务的基础数据结构。

    Task 描述任务目标与约束，不保存执行过程中的中间状态。
    """

    task_id: str = Field(description="任务唯一标识，用于追踪一次完整的任务执行过程")
    objective: str = Field(description="任务目标，即当前执行者需要完成的具体工作")
    task_context: dict[str, Any] = Field(
        default_factory=dict,
        description="执行任务所需的外部上下文，例如用户提供的条件、业务背景或上游任务信息",
    )
    input_refs: list[DataRef] = Field(
        default_factory=list, description="用户提供的输入数据引用"
    )
    constraints: dict[str, Any] = Field(
        default_factory=dict, description="任务执行过程中需要遵守的约束条件"
    )


class BaseAgentTask(BaseTask):
    """
    Agent 执行的任务。
    """

    agent_name: str = Field(description="目标子 Agent 名称，例如 spatial_agent")
    required_capabilities: list[str] = Field(
        default_factory=list, description="完成当前任务所需要的能力或技能"
    )  # 待修改，skill是什么格式
    # expected_outputs: list[str] = Field(
    #     default_factory=list,
    #     description="主 Agent 期望收到的事实类型或结论类型",
    # )  # 待定


class BaseSkillTask(BaseTask):
    """
    Skill 所需的输入任务，子Agent发送给skill的任务。

    Skill 输入任务与 Agent 任务类似，
    但 Skill 输入任务不包含任务执行过程中所使用的工具。
    """

    skill_name: str = Field(description="目标 Skill 名称，例如 spatial_skill")
    parent_agent_task_id: str = Field(description="所属 AgentTask 的 task_id")
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="该 Skill 本次允许调用的工具名称；为空表示由 Skill 自行决定",
    )
    required_evidence: list[str] = Field(
        default_factory=list,
        description="Skill 必须产出的事实类型，例如 intersects、area、count、risk_level",
    )


class BaseAgentEvidence(BaseModel):
    """子 Agent 汇总多个 SkillEvidence 后交给主 Agent 的证据包。"""

    task_id: str = Field(description="当前 Agent 所属任务的唯一标识")
    source_agent: str = Field(description="产生证据的子 Agent 名称")
    status: Literal["success", "partial", "failed"] = Field(
        description="Agent 执行状态"
    )
    summary: str = Field(description="当前 Agent 对执行结果的简要概括")
    facts: list[str] = Field(
        default_factory=list, description="当前 Agent 产生的关键事实"
    )
    errors: list[str] = Field(default_factory=list)
