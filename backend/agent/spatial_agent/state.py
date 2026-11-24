"""空间子图的运行状态；数据库连接、Skill 实例和大规模 GIS 数据不放入 State。

节点返回字段更新字典，不直接修改传入的 State。
并行 Skill 节点只更新自己的 step_states 条目；其余字段由串行调度/核验节点更新。
State 本身不执行持久化，后续在图编译时配置 checkpointer。
当前 Python 3.10 + LangGraph 0.3.34 应通过 graph.invoke(state) 传入模型对象；
JSON 数据先用 SpatialAgentState.model_validate_json 恢复，避免旧版嵌套字典转换问题。
"""

from datetime import datetime
from typing import Annotated, Dict, List, Literal

from pydantic import BaseModel, Field

# 当前 Python 3.10 + LangGraph 0.3 使用 List/Dict，避开内置泛型的类型转换兼容问题。

from backend.agent.base_schemas import DataRef
from backend.agent.spatial_agent.schemas import (
    SkillPlan,
    SpatialAgentEvidence,
    SpatialAgentTask,
)
from backend.skills.quantitative_skill.schemas import QuantitativeSkillEvidence
from backend.skills.spatial_skill.schemas import SpatialSkillEvidence


class ReviewResult(BaseModel):
    """记录一步或整个任务的核验结论，以及下一步处理建议。"""

    status: Literal["pending", "passed", "failed"] = "pending"
    reason: str = Field(default="", description="核验依据，例如输出缺失、坐标系或单位不符。")
    action: Literal["continue", "retry", "human", "finish", "fail"] | None = Field(
        default=None, description="核验节点的处理决定；未核验时为空。"
    )


class ExecutionIssue(BaseModel):
    """当前尚未解决的问题；解决后由核验节点从当前问题列表移除。"""

    step_id: str | None = Field(default=None, description="关联步骤；任务级问题为空。")
    message: str = Field(description="问题描述及需要补充的信息。")
    retryable: bool = Field(default=False, description="是否适合自动重试，不能仅根据此值立即重试。")


class SkillAttempt(BaseModel):
    """一次已结束的 Skill 调用摘要，保留重试前的证据和核验结果。"""

    attempt: int = Field(ge=1, description="调用次数，从 1 开始，包含首次调用。")
    status: Literal["success", "partial", "failed"]
    evidence: SpatialSkillEvidence | QuantitativeSkillEvidence | None = None
    error: str | None = None
    review: ReviewResult = Field(default_factory=ReviewResult)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    record_id: str | None = Field(default=None, description="完整执行记录落库后的 ID。")


class SkillExecutionState(BaseModel):
    """一个计划步骤的当前进度及历次调用摘要，由该步骤的执行者独占更新。"""

    status: Literal["pending", "running", "success", "partial", "failed", "skipped"] = "pending"
    attempts: int = Field(default=0, ge=0, description="已启动调用次数；首次调用前为 0。")
    review: ReviewResult = Field(default_factory=ReviewResult)
    evidence: SpatialSkillEvidence | QuantitativeSkillEvidence | None = Field(
        default=None, description="最近一次调用的证据；重试开始时清空，旧证据保存在 history。"
    )
    output_refs: List[DataRef] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list, description="历次错误摘要。")
    history: List[SkillAttempt] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RecoveryRecord(BaseModel):
    """核验节点记录的恢复措施及结果，包括重试和人工处置。"""

    step_id: str
    reason: str
    action: str
    outcome: str
    attempt: int = Field(default=1, ge=1)


class HumanReview(BaseModel):
    """交给人工的问题及收到的反馈；实际暂停和恢复由图节点实现。"""

    status: Literal["not_required", "waiting", "answered"] = "not_required"
    step_ids: List[str] = Field(default_factory=list)
    question: str | None = None
    feedback: str | None = None
    decision: Literal["retry", "accept", "abort"] | None = None


def merge_step_states(
    current: Dict[str, SkillExecutionState],
    updates: Dict[str, SkillExecutionState],
) -> Dict[str, SkillExecutionState]:
    """按步骤 ID 合并并行更新，避免不同 Skill 的结果相互覆盖。

    每个更新值必须是该步骤的完整记录，而非嵌套字段的局部更新。
    同一步骤不能同时派给两个节点；串行重试可以替换它的旧记录。
    """
    return {**current, **updates}


class SpatialAgentState(BaseModel):
    """一次 Supervisor 委托在空间子图中的状态，可用作 StateGraph 的状态类型。

    current_step_ids / next_step_ids 是调度节点写入的执行批次快照。
    修改计划后应重新计算批次；首次版本暂不支持执行中替换计划。
    """

    task: SpatialAgentTask
    run_id: str = Field(min_length=1, description="本次运行 ID，由调用方创建，用于关联数据库记录。")
    session_id: str | None = Field(default=None, description="用户会话 ID；完整会话由 Supervisor 管理。")
    status: Literal[
        "pending", "planning", "executing", "reviewing", "retrying",
        "waiting_human", "success", "partial", "failed",
    ] = "pending"
    skill_plan: SkillPlan | None = None
    step_states: Annotated[Dict[str, SkillExecutionState], merge_step_states] = Field(
        default_factory=dict, description="以计划 step_id 为键的步骤执行记录。"
    )
    current_step_ids: List[str] = Field(default_factory=list, description="本批正在执行或等待核验的步骤。")
    next_step_ids: List[str] = Field(default_factory=list, description="已选定、下一批准备派发的步骤。")
    execution_mode: Literal["parallel", "sequential"] = Field(
        default="parallel", description="parallel 允许就绪步骤并行；sequential 每次只取一个。"
    )
    max_retries: int = Field(default=2, ge=0, description="每步最多额外重试次数，不包含首次调用。")
    current_issues: List[ExecutionIssue] = Field(default_factory=list)
    review: ReviewResult = Field(default_factory=ReviewResult, description="整个任务的最终核验。")
    human_review: HumanReview = Field(default_factory=HumanReview)
    recovery_history: List[RecoveryRecord] = Field(default_factory=list)
    final_result: SpatialAgentEvidence | None = Field(default=None, description="汇总后交回 Supervisor 的结果。")

    def get_ready_step_ids(self) -> List[str]:
        """取得依赖已满足的候选步骤；不修改 State，也不实际派发 Skill。

        调度节点将返回值写入 next_step_ids，派发时移入 current_step_ids。
        核验失败的步骤必须明确获准重试且未超限，才能再次成为候选。
        """
        if self.skill_plan is None or self.status in {
            "waiting_human", "success", "partial", "failed",
        }:
            return []
        ready = []
        for step in self.skill_plan.steps:
            record = self.step_states.get(step.step_id, SkillExecutionState())
            if record.status == "pending":
                eligible = record.attempts == 0
            else:
                eligible = (
                    record.status in {"success", "partial", "failed"}
                    and record.review.status == "failed"
                    and record.review.action == "retry"
                    and 0 < record.attempts <= self.max_retries
                )
            if not eligible:
                continue
            # 仅执行成功还不够，前置证据必须经过核验；跳过不能视作依赖已满足。
            if all(
                parent in self.step_states
                and self.step_states[parent].status in {"success", "partial"}
                and self.step_states[parent].review.status == "passed"
                for parent in step.depends_on
            ):
                ready.append(step.step_id)
        return ready[:1] if self.execution_mode == "sequential" else ready
