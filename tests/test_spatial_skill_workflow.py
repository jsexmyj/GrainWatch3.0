import asyncio
import csv
import json
import os
import sys
from pathlib import Path
from typing import AsyncGenerator, Dict, Any

import pytest
import pytest_asyncio

# 1. 自动处理项目根目录路径
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from backend.infrastructure.llm.plan_strategy_factory import (
    create_siliconflow_plan_strategy,
)
from backend.infrastructure.tool_manager.loader import ToolLoader
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.skills.planner_llm_strategy import PlanLLMConfig
from backend.skills.spatial_skill.executor import SpatialExecutor
from backend.skills.spatial_skill.planner import SpatialSkillPlanner
from backend.skills.spatial_skill.schemas import SpatialSkillTask
from backend.skills.spatial_skill.spatial_skill import SpatialSkill
from backend.utils.paths import PATHS

# ==========================================
# Pytest Fixtures (实现依赖注入与严格隔离)
# ==========================================


@pytest.fixture(scope="session")
def test_dirs() -> Dict[str, Path]:
    """管理测试所需的公共目录数据"""
    data_dir = PROJECT_ROOT / "data" / "shp数据" / "国外测试数据点线面（AI测试用）"
    output_dir = PROJECT_ROOT / "data" / "运行结果"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_output_path = (
        PROJECT_ROOT / "tests" / "0901spatial_planner_difficulty_results.csv"
    )

    return {
        "points": str(data_dir / "点数据.shp"),
        "boundary": str(data_dir / "行政边界数据.shp"),
        "public_area": str(data_dir / "公共区域.shp"),
        "roads": str(data_dir / "公路数据.shp"),
        "output_dir": str(output_dir),
        "csv_output": csv_output_path,
    }


@pytest.fixture(scope="function")
def fresh_registry() -> ToolRegistry:
    """每个测试函数执行前，提供一个全新孤立的 Registry 实例"""
    registry = ToolRegistry()
    ToolLoader.auto_load_tools(
        registry=registry,
        package_path=PATHS.infrastructure_path("gis", "vector"),
    )
    return registry


def make_task(
    task_id: str, objective: str, difficulty: str, dirs: Dict[str, Path]
) -> SpatialSkillTask:
    """任务构建辅助函数"""
    return SpatialSkillTask(
        task_id=task_id,
        objective=objective,
        task_context={"source": "unit_test"},
        constraints={},
        skill_name="spatial_skill",
        parent_agent_task_id="agent-task-test",
        allowed_tools=["vector_load", "vector_reproject", "buffer", "vector_write"],
        required_evidence=["tool_chain", "execution_status"],
        analysis_scope={
            "difficulty": difficulty,
            "crs": "EPSG:4326",
            "dataset_paths": {
                "points": dirs["points"],
                "boundary": dirs["boundary"],
                "public_area": dirs["public_area"],
                "roads": dirs["roads"],
            },
            "output_dir": dirs["output_dir"],
        },
    )


# ==========================================
# 测试用例主体
# ==========================================


@pytest.mark.asyncio
async def test_01_siliconflow_plan_strategy_json_mode():
    """测试 LLM JSON 模式生成结构化 Payload"""
    config = PlanLLMConfig(
        provider="siliconflow",
        model="deepseek-ai/DeepSeek-V3.2",
        mode="json",
        temperature=0.0,
        max_tokens=512,
        json_mode_supported=True,
        tool_name="arithmetic_calculator",
    )
    strategy = create_siliconflow_plan_strategy(config=config)

    arithmetic_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["add", "subtract"]},
            "a": {"type": "number"},
            "b": {"type": "number"},
            "result": {"type": "number"},
        },
        "required": ["operation", "a", "b", "result"],
    }

    payload = await strategy.generate_plan_payload(
        prompt="请用 JSON 返回一个加法示例。",
        plan_schema=arithmetic_schema,
    )

    # 修改断言方式：校验 Schema 结构的字段合法性，而不是校验具体计算数值
    assert "operation" in payload
    assert payload["operation"] in ["add", "subtract"]
    assert "result" in payload


@pytest.mark.asyncio
async def test_02_siliconflow_plan_strategy_tool_calling_mode():
    """测试 LLM Tool Calling 模式生成"""
    config = PlanLLMConfig(
        provider="siliconflow",
        model="deepseek-ai/DeepSeek-V3.2",
        mode="tool_calling",
        temperature=0.0,
        max_tokens=512,
        json_mode_supported=False,
        tool_name="arithmetic_calculator",
    )
    strategy = create_siliconflow_plan_strategy(config=config)

    arithmetic_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["add", "subtract"]},
            "a": {"type": "number"},
            "b": {"type": "number"},
            "result": {"type": "number"},
        },
        "required": ["operation", "a", "b", "result"],
    }

    payload = await strategy.generate_plan_payload(
        prompt="请用 tool calling 返回一个减法示例。",
        plan_schema=arithmetic_schema,
    )

    assert "operation" in payload


@pytest.mark.asyncio
async def test_03_spatial_skill_planner_difficulty_and_csv(fresh_registry, test_dirs):
    """测试 SpatialSkillPlanner 的难度调度与 CSV 输出"""
    config = PlanLLMConfig(
        provider="siliconflow",
        model="deepseek-ai/DeepSeek-V3.2",
        mode="tool_calling",
        temperature=0.0,
        max_tokens=2048,
        tool_name="create_tool_plan",
    )
    llm_strategy = create_siliconflow_plan_strategy(config=config)

    planner = SpatialSkillPlanner(
        registry=fresh_registry,
        llm_strategy=llm_strategy,
        tool_directory=(PATHS.infrastructure_path("gis", "vector"),),
    )

    scenarios = [
        (
            "low",
            "请直接使用工具把点数据加载、缓冲10m并输出结果。",
            {"vector_load", "buffer", "vector_write"},
        ),
        (
            "medium",
            "请完成行政边界的缓冲结果输出，步骤由你自己补全。",
            {"vector_load", "vector_reproject", "buffer", "vector_write"},
        ),
        (
            "high",
            "业务场景：评估空气质量监测点周边 1km 影响区，并考虑道路要素参与分析准备。",
            {"vector_load", "vector_reproject", "buffer", "vector_write"},
        ),
    ]

    rows = []
    for idx, (difficulty, question, expected_tools) in enumerate(scenarios, start=1):
        task = make_task(f"planner-task-{idx}", question, difficulty, test_dirs)
        plan = await planner.plan(task)

        planned_tools = [step.tool_name for step in plan.steps]
        planned_set = set(planned_tools)
        matched = len(expected_tools & planned_set)
        completion = (
            f"{matched}/{len(expected_tools)} ({matched / len(expected_tools):.0%})"
        )

        rows.append(
            {
                "difficulty": difficulty,
                "question": question,
                "tool_calls": " -> ".join(planned_tools),
                "completion": completion,
                "plan_id": plan.plan_id,
                "step_count": str(len(plan.steps)),
            }
        )

        assert matched >= 1
        assert len(plan.steps) > 0

    csv_path = test_dirs["csv_output"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "difficulty",
                "question",
                "tool_calls",
                "completion",
                "plan_id",
                "step_count",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    assert csv_path.exists()


@pytest.mark.asyncio
async def test_04_execute_generated_toolplan(fresh_registry, test_dirs):
    """测试 Executor 执行规划出的步骤"""
    config = PlanLLMConfig(
        provider="siliconflow",
        model="deepseek-ai/DeepSeek-V3.2",
        mode="tool_calling",
        temperature=0.5,
        max_tokens=2048,
        tool_name="create_tool_plan",
    )
    llm_strategy = create_siliconflow_plan_strategy(config=config)
    planner = SpatialSkillPlanner(
        registry=fresh_registry,
        llm_strategy=llm_strategy,
        tool_directory=(PATHS.infrastructure_path("gis", "vector"),),
    )

    task = make_task(
        "execute-toolplan-task",
        "请完成行政边界缓冲输出,缓冲距离10米，必要时自动补齐步骤。",
        "medium",
        test_dirs,
    )
    plan = await planner.plan(task)

    tool_manager = ToolManager(fresh_registry)
    executor = SpatialExecutor(tool_manager)
    execution = await executor.execute(plan)

    assert execution.results
    assert execution.success


@pytest.mark.asyncio
async def test_05_spatial_skill_execute_end_to_end(fresh_registry, test_dirs):
    """测试 SpatialSkill 端到端执行流程"""
    config = PlanLLMConfig(
        provider="siliconflow",
        model="deepseek-ai/DeepSeek-V3.2",
        mode="tool_calling",
        temperature=0.0,
        max_tokens=2048,
        tool_name="create_tool_plan",
    )
    llm_strategy = create_siliconflow_plan_strategy(config=config)
    planner = SpatialSkillPlanner(
        registry=fresh_registry,
        llm_strategy=llm_strategy,
        tool_directory=(PATHS.infrastructure_path("gis", "vector"),),
    )
    executor = SpatialExecutor(ToolManager(fresh_registry))

    spatial_skill = SpatialSkill(
        planner=planner,
        executor=executor,
        registry=fresh_registry,
        load_tools=False,  # 显式禁止 Skill 重复加载 Tool 导致冲突
    )

    task = make_task(
        "spatial-skill-e2e-task",
        "业务场景：请识别空气质量监测点影响区并产出可落盘结果。",
        "high",
        test_dirs,
    )
    evidence = await spatial_skill.execute(task)

    assert evidence.source_skill == "spatial_skill"
    assert evidence.status in ["success", "partial"]
    assert len(evidence.facts) > 0


if __name__ == "__main__":
    import pytest

    # 参数说明：
    # -v: 详细模式（verbose）
    # -s: 允许显示 print 输出（不捕获控制台日志）
    # --tb=short: 报错时展示简洁明了的错误堆栈信息（如需极详细堆栈可改成 --tb=long）
    # -k: 匹配指定的测试方法名

    print("=== 开始按顺序单测试运行 ===")

    # 1. 如果只想要【单独运行某一个】测试（例如只运行 test_02）：
    pytest.main(
        [
            __file__,
            "-v",
            "-s",
            "--tb=short",
            "-k",
            "test_04_execute_generated_toolplan",
        ]
    )

    # 2. 如果想要【按照名称顺序依次执行所有测试】，并在遇到第一个错误时立即停止调试（加 -x）：
    # pytest.main([__file__, "-v", "-s", "--tb=short", "-x"])
