"""空间子图状态的基础测试，不调用 LLM、真实 Skill 或数据库。"""

from pathlib import Path
import sys

from langgraph.graph import END, START, StateGraph

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.agent.spatial_agent.schemas import SkillPlan, SkillStep, SpatialAgentTask
from backend.agent.spatial_agent.state import (
    ReviewResult,
    SkillExecutionState,
    SpatialAgentState,
)
from backend.skills.quantitative_skill.schemas import QuantitativeSkillEvidence


def make_state() -> SpatialAgentState:
    """准备 A、B 可并行，C 依赖 A 的小计划。"""
    return SpatialAgentState(
        task=SpatialAgentTask(task_id="task-1", objective="空间分析"),
        run_id="run-1",
        skill_plan=SkillPlan(
            steps=[
                SkillStep(step_id="a", skill_name="spatial_skill", objective="裁剪"),
                SkillStep(
                    step_id="b", skill_name="quantitative_skill", objective="独立统计"
                ),
                SkillStep(
                    step_id="c",
                    skill_name="quantitative_skill",
                    objective="裁剪后统计",
                    depends_on=["a"],
                ),
            ]
        ),
    )


def test_dependencies_require_review():
    """执行成功但未核验时，不能放行后续依赖步骤。"""
    state = make_state()
    assert state.get_ready_step_ids() == ["a", "b"]
    state.step_states["a"] = SkillExecutionState(status="success", attempts=1)
    assert state.get_ready_step_ids() == ["b"]
    state.step_states["a"].review = ReviewResult(status="passed", action="continue")
    assert state.get_ready_step_ids() == ["b", "c"]
    state.execution_mode = "sequential"
    assert state.get_ready_step_ids() == ["b"]


def test_retry_limit_and_human_pause():
    """重试须经过核验节点授权，达到上限或等待人工时不可继续派发。"""
    state = make_state()
    record = SkillExecutionState(status="failed", attempts=1)
    state.step_states["a"] = record
    assert "a" not in state.get_ready_step_ids()
    record.review = ReviewResult(status="failed", action="retry")
    assert "a" in state.get_ready_step_ids()
    record.attempts = 3  # 首次调用加两次重试，已经用完预算。
    assert "a" not in state.get_ready_step_ids()
    state.status = "waiting_human"
    assert state.get_ready_step_ids() == []


def test_evidence_json_round_trip():
    """保存、恢复状态时，量化证据的专有统计字段不能丢失。"""
    state = make_state()
    state.step_states["b"] = SkillExecutionState(
        status="success",
        attempts=1,
        evidence=QuantitativeSkillEvidence(
            task_id="skill-task-b",
            source_skill="quantitative_skill",
            status="success",
            summary="面积统计",
            statistics_summary={"area": 12},
        ),
    )
    restored = SpatialAgentState.model_validate_json(state.model_dump_json())
    assert isinstance(restored.step_states["b"].evidence, QuantitativeSkillEvidence)
    assert restored.step_states["b"].evidence.statistics_summary == {"area": 12}


def test_parallel_graph_merges_step_records():
    """真实 LangGraph 中的两个并行节点分别写入，最终必须同时保留。"""
    builder = StateGraph(SpatialAgentState)
    builder.add_node(
        "first",
        lambda state: {
            "step_states": {"a": SkillExecutionState(status="success", attempts=1)}
        },
    )
    builder.add_node(
        "second",
        lambda state: {
            "step_states": {"b": SkillExecutionState(status="success", attempts=1)}
        },
    )
    builder.add_edge(START, "first")
    builder.add_edge(START, "second")
    builder.add_edge("first", END)
    builder.add_edge("second", END)
    # 传入 Pydantic 状态，保留嵌套模型；数据库 JSON 应先 model_validate_json 恢复。
    result = builder.compile().invoke(make_state())
    assert set(result["step_states"]) == {"a", "b"}


def test_states_do_not_share_mutable_defaults():
    """两个委托任务的列表和字典必须相互独立。"""
    first, second = make_state(), make_state()
    first.current_step_ids.append("a")
    first.step_states["a"] = SkillExecutionState()
    assert second.current_step_ids == []
    assert second.step_states == {}


if __name__ == "__main__":
    test_dependencies_require_review()
    test_retry_limit_and_human_pause()
    test_evidence_json_round_trip()
    test_parallel_graph_merges_step_records()
    test_states_do_not_share_mutable_defaults()
