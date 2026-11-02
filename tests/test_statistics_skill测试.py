"""Statistics / QuantitativeSkill 功能集成测试（默认真实 LLM）。

运行：python -m pytest "tests/test_statistics_skill测试.py" -v -s
阅读：同目录的 03-statistics_skill测试说明.md。

pytest 收集 test_ 开头的函数，通过同名参数注入 fixture，用 assert 判断结果。
本文件不在导入时执行任务；真实模型调用、GIS 读取和临时文件写入均发生在测试时。
测试 4 有两个子场景，所以共 5 组、6 个测试项。
"""

import asyncio
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from unittest.mock import AsyncMock

import geopandas as gpd
import pytest
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agent.base_schemas import DataRef
from backend.infrastructure.llm.plan_strategy_factory import (
    create_siliconflow_plan_strategy,
)
from backend.infrastructure.tool_manager.loader import ToolLoader
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.skills.base_schemas import ToolPlan, ToolStep
from backend.skills.planner_llm_strategy import PlanLLMConfig
from backend.skills.quantitative_skill.errors import QuantitativeDataError
from backend.skills.quantitative_skill.excutor import QuantitativeExecutor
from backend.skills.quantitative_skill.planner import QuantitativeSkillPlanner
from backend.skills.quantitative_skill.quantitative_skill import QuantitativeSkill
from backend.skills.quantitative_skill.references import plan_dependencies
from backend.skills.quantitative_skill.schemas import (
    QuantitativeSkillEvidence,
    QuantitativeSkillTask,
)
from backend.utils.paths import PATHS

DATA_DIR = PROJECT_ROOT / "data" / "shp数据" / "国外测试数据点线面（AI测试用）"
CLASS_FIELD = "pm25_class"
VALUE_FIELD = "pm25_mean_"  # Shapefile 中的实际字段名，不是 pm25_mean。
AREA_FIELD = "test_area"
TIMEOUT_SECONDS = 120


@pytest.fixture
def registry():
    """fixture：每个测试获得独立注册表，加载真实统计工具。"""
    result = ToolRegistry()
    ToolLoader.auto_load_tools(result, PATHS.infrastructure_path("gis", "statistics"))
    assert {"value_counts", "group_aggregate", "vector_area"} <= set(
        result.list_all_tools()
    )
    return result


def read_vector(filename):
    path = DATA_DIR / filename
    for suffix in (".shp", ".shx", ".dbf", ".prj"):
        assert path.with_suffix(suffix).is_file(), f"测试数据缺失：{path.with_suffix(suffix)}"
    frame = gpd.read_file(path)
    assert not frame.empty, f"测试数据为空：{path}"
    assert frame.crs is not None, f"测试数据缺少 CRS：{path}"
    assert frame.geometry.notna().all() and not frame.geometry.is_empty.any()
    assert frame.geometry.is_valid.all(), f"测试数据有无效几何：{path}"
    return path, frame


@pytest.fixture
def points():
    path, frame = read_vector("点数据.shp")
    assert {CLASS_FIELD, VALUE_FIELD} <= set(frame.columns)
    assert frame.geometry.geom_type.isin(["Point", "MultiPoint"]).all()
    assert frame[[CLASS_FIELD, VALUE_FIELD]].notna().all().all()
    assert all(math.isfinite(float(value)) for value in frame[VALUE_FIELD])
    return path, frame


@pytest.fixture
def boundary_projected(tmp_path):
    """原数据为 WGS84；预先投影临时副本，模拟上游完成坐标转换。

    重投影不是 statistics skill 的职责。tmp_path 由 pytest 为每个测试提供。
    """
    _, frame = read_vector("行政边界数据.shp")
    assert "ward_name" in frame.columns
    assert frame["ward_name"].notna().all()
    assert frame.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all()
    target_crs = frame.estimate_utm_crs()
    assert target_crs is not None, "无法从边界范围推断 UTM 投影"
    projected = frame.to_crs(target_crs)
    assert projected.crs.is_projected
    assert all(axis.unit_conversion_factor == 1 for axis in projected.crs.axis_info)
    path = tmp_path / "boundary_projected.shp"
    projected.to_file(path, driver="ESRI Shapefile", index=False, encoding="UTF-8")
    return path, projected


@pytest.fixture
def live_planner(registry):
    """真实模型 fixture：直接使用项目工厂创建规划策略。

    缺少配置直接失败，不静默跳过或降级为假模型；不输出密钥。
    """
    load_dotenv(".env", override=False)
    config = PlanLLMConfig(
        provider="siliconflow",
        model="deepseek-ai/DeepSeek-V3.2",
        mode="tool_calling",
        temperature=0.0,
        max_tokens=2048,
    )
    strategy = create_siliconflow_plan_strategy(config=config)
    return QuantitativeSkillPlanner(registry, strategy)


def make_task(task_id, objective, dataset, data_id, allowed_tools=None):
    path, frame = dataset
    # 只提供字段和 CRS 等规划信息，不把属性记录或预期答案塞给模型。
    return QuantitativeSkillTask(
        task_id=task_id,
        objective=objective,
        skill_name="quantitative_skill",
        parent_agent_task_id="statistics-integration",
        allowed_tools=allowed_tools or [],
        input_refs=[DataRef(
            data_id=data_id,
            data_type="vector",
            data_path=str(path),
            data_name=path.name,
            metadata={
                "columns": list(frame.columns),
                "crs": str(frame.crs),
                "geometry_types": sorted(frame.geometry.geom_type.unique().tolist()),
            },
        )],
    )


def assert_plan_contract(plan, task, registry):
    """结构、参数名、引用与依赖契约；业务含义由各场景另外断言。"""
    assert isinstance(plan, ToolPlan)
    ToolPlan.model_validate_json(plan.model_dump_json())
    assert plan.metadata["task_id"] == task.task_id
    assert plan.metadata["execution_mode"] == "dependency_parallel"
    assert plan.steps
    for step in plan.steps:
        tool = registry.get_tool(step.tool_name)
        assert tool is not None
        if task.allowed_tools:
            assert step.tool_name in task.allowed_tools
        fields = tool.input_model.model_fields
        required = {name for name, field in fields.items() if field.is_required()}
        assert required <= step.arguments.keys()
        assert step.arguments.keys() <= fields.keys()
        assert step.on_failure in {"raise", "stop"}, "本场景要求数据错误立即阻断"
    # 该检查会拒绝循环、悬空步骤引用和上下文键冲突。
    plan_dependencies(plan)


def assert_input_source(value, task):
    ref = task.input_refs[0]
    if value == "${inputs." + ref.data_id + "}":
        return
    assert isinstance(value, str) and not value.startswith("${")
    assert (PROJECT_ROOT / value).resolve() == Path(ref.data_path).resolve()


def assert_count_plan(plan, task):
    """允许两种等价计数工具，不绑定模型生成的 step_id 或 plan_id。"""
    assert len(plan.steps) == 1, "仅统计类别数量应使用一个统计步骤"
    step = plan.steps[0]
    assert_input_source(step.arguments.get("gdf"), task)
    if step.tool_name == "value_counts":
        assert step.arguments["field"] == CLASS_FIELD
        assert step.arguments.get("top_n") is None, "不得截断类别"
    else:
        assert step.tool_name == "group_aggregate"
        assert step.arguments["group_by"] == CLASS_FIELD
        assert step.arguments["aggregate"] == "count"
        assert step.arguments.get("metric") in {None, CLASS_FIELD}


def independent_means(frame):
    # 用 Python 分桶求均值，避免调用被测 group_aggregate 生成预期答案。
    groups = defaultdict(list)
    for category, value in zip(frame[CLASS_FIELD], frame[VALUE_FIELD]):
        groups[category].append(float(value))
    return {category: mean(values) for category, values in groups.items()}


def polygon_area(geometry):
    """独立鞋带公式，计算投影坐标下的平面面积；减去内环、累加多面。

    坐标先平移以减小大坐标相减误差，不复用生产工具的 geometry.area。
    """
    def ring_area(ring):
        coords = list(ring.coords)
        x0, y0 = coords[0][:2]
        shifted = [(p[0] - x0, p[1] - y0) for p in coords]
        return abs(math.fsum(
            a[0] * b[1] - b[0] * a[1]
            for a, b in zip(shifted, shifted[1:])
        )) / 2

    if geometry.geom_type == "MultiPolygon":
        return math.fsum(polygon_area(part) for part in geometry.geoms)
    assert geometry.geom_type == "Polygon"
    return ring_area(geometry.exterior) - math.fsum(
        ring_area(ring) for ring in geometry.interiors
    )


@pytest.mark.asyncio
async def test_01_planner_count_contract(live_planner, registry, points, monkeypatch):
    """测试 1：只规划；检查自然语言目标、ToolPlan 契约和统计语义。"""
    task = make_task("statistics-01", "统计不同 pm25_class 下监测点的数量，返回全部类别。",
                     points, "points")
    # 如果 Planner 越界调用执行器/统计工具，测试必须失败。
    forbidden = AsyncMock(side_effect=AssertionError("Planner 阶段不得执行工具"))
    monkeypatch.setattr(QuantitativeExecutor, "prepare", forbidden)
    monkeypatch.setattr(QuantitativeExecutor, "execute", forbidden)
    monkeypatch.setattr(ToolManager, "execute", forbidden)
    plan = await asyncio.wait_for(live_planner.plan(task), TIMEOUT_SECONDS)
    assert_plan_contract(plan, task, registry)
    assert_count_plan(plan, task)
    forbidden.assert_not_awaited()
    print("\n测试 1 ToolPlan：", plan.model_dump_json(indent=2))


@pytest.mark.asyncio
async def test_02_point_area_blocked_before_execution(
    live_planner, registry, points, monkeypatch,
):
    """测试 2：点求面积在 prepare 数据验证阶段失败，不能等工具返回 0。"""
    task = make_task("statistics-02", "计算空气质量监测点数据的总面积。",
                     points, "points", ["vector_area"])
    executor = QuantitativeExecutor(ToolManager(registry))
    skill = QuantitativeSkill(live_planner, executor=executor, load_tools=False)
    # wraps 是 spy：记录调用并运行真实函数，保留真实模型与真实预检。
    plan_spy = AsyncMock(wraps=live_planner.plan)
    prepare_spy = AsyncMock(wraps=executor.prepare)
    execute_spy = AsyncMock(wraps=executor.execute)
    tool_spy = AsyncMock(wraps=executor.tool_manager.execute)
    monkeypatch.setattr(live_planner, "plan", plan_spy)
    monkeypatch.setattr(executor, "prepare", prepare_spy)
    monkeypatch.setattr(executor, "execute", execute_spy)
    monkeypatch.setattr(executor.tool_manager, "execute", tool_spy)

    with pytest.raises(QuantitativeDataError, match="不支持的几何类型") as caught:
        await asyncio.wait_for(skill.execute(task), TIMEOUT_SECONDS)

    plan_spy.assert_awaited_once()
    prepare_spy.assert_awaited_once()
    execute_spy.assert_not_awaited()
    tool_spy.assert_not_awaited()
    plan = prepare_spy.await_args.args[0]
    assert_plan_contract(plan, task, registry)
    assert len(plan.steps) == 1 and plan.steps[0].tool_name == "vector_area"
    assert_input_source(plan.steps[0].arguments["gdf"], task)
    error = caught.value
    assert error.category == "data"
    assert error.step_id == plan.steps[0].step_id
    assert error.tool_name == "vector_area"
    assert "Point" in error.reason
    print("\n测试 2 预期阻断：", str(error))


@pytest.mark.asyncio
async def test_03_executor_boundary_area(registry, boundary_projected):
    """测试 3：隔离 Planner，给真实 Executor 一个明确计划并核对面积。"""
    task = make_task("statistics-03", "计算行政边界区域的面积。",
                     boundary_projected, "boundary")
    plan = ToolPlan(plan_id="area-executor", steps=[ToolStep(
        step_id="area", tool_name="vector_area",
        arguments={"gdf": "${inputs.boundary}", "area_field_name": AREA_FIELD,
                   "decimals": None},
    )])
    execution = await QuantitativeExecutor(ToolManager(registry)).execute(plan, task.input_refs)
    assert execution.success and execution.failed_step_id is None
    assert len(execution.results) == 1 and not execution.warnings
    result = execution.results[0]
    assert result.success and result.tool_name == "vector_area"
    assert result.result_type == "vector" and result.fact
    output = result.data
    original = boundary_projected[1]
    expected = [polygon_area(geom) for geom in original.geometry]
    assert isinstance(output, gpd.GeoDataFrame)
    assert output.crs == original.crs
    assert output["ward_name"].tolist() == original["ward_name"].tolist()
    # Shapefile 写入可能调整环的起点/方向；检查几何等价而非二进制排列。
    assert output.geometry.geom_equals(original.geometry).all()
    assert output[AREA_FIELD].tolist() == pytest.approx(expected, rel=1e-9, abs=1e-5)
    assert float(output[AREA_FIELD].sum()) == pytest.approx(math.fsum(expected), rel=1e-9)
    assert all(value > 0 for value in expected)
    assert result.metadata["feature_count"] == len(original)
    assert execution.context["area"] is result
    assert AREA_FIELD not in original.columns
    print(f"\n测试 3 总面积：{output[AREA_FIELD].sum():.6f} 平方米")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["pm25_mean", "area_dependency_chain"])
async def test_04_planner_executor(live_planner, registry, points, request, scenario):
    """测试 4：业务均值/最高类别，以及真实的跨工具数据依赖。

    group_aggregate 已能降序返回均值，不能虚构未注册的 top-N 工具，
    也不能将其 DataFrame 输出传入只接受 GeoDataFrame 的工具。
    因而用面积 -> 分组求和补充覆盖多工具依赖链。
    """
    if scenario == "pm25_mean":
        task = make_task(
            "statistics-04-mean",
            "计算不同 pm25_class 的平均 PM2.5 浓度（字段 pm25_mean_），"
            "按平均浓度从高到低返回全部类别，使第一行代表平均浓度最高的类别。",
            points, "points",
        )
    else:
        boundary = request.getfixturevalue("boundary_projected")
        task = make_task(
            "statistics-04-chain",
            f"先计算各行政边界要素的面积，写入 {AREA_FIELD} 字段，"
            "再按 ward_name 分组汇总该面积，按总面积降序返回所有行政区。",
            boundary, "boundary",
        )
    plan = await asyncio.wait_for(live_planner.plan(task), TIMEOUT_SECONDS)
    assert_plan_contract(plan, task, registry)
    aggregate_steps = [s for s in plan.steps if s.tool_name == "group_aggregate"]
    assert len(aggregate_steps) == 1
    aggregate_step = aggregate_steps[0]
    args = aggregate_step.arguments
    assert args.get("ascending", False) is False
    if scenario == "pm25_mean":
        assert len(plan.steps) == 1
        assert_input_source(args["gdf"], task)
        assert args["group_by"] == CLASS_FIELD and args["metric"] == VALUE_FIELD
        assert args["aggregate"] == "mean"
        expected = independent_means(points[1])
    else:
        assert len(plan.steps) == 2
        area_steps = [s for s in plan.steps if s.tool_name == "vector_area"]
        assert len(area_steps) == 1
        area_step = area_steps[0]
        assert_input_source(area_step.arguments["gdf"], task)
        assert area_step.arguments["area_field_name"] == AREA_FIELD
        assert args["gdf"] == "${steps." + area_step.step_id + ".data}"
        assert args["group_by"] == "ward_name" and args["metric"] == AREA_FIELD
        assert args["aggregate"] == "sum"
        assert plan_dependencies(plan)[aggregate_step.step_id] == {area_step.step_id}
        expected = defaultdict(float)
        for ward, geom in zip(boundary[1]["ward_name"], boundary[1].geometry):
            expected[ward] += polygon_area(geom)

    execution = await QuantitativeExecutor(ToolManager(registry)).execute(plan, task.input_refs)
    assert execution.success and execution.failed_step_id is None
    assert len(execution.results) == len(plan.steps)
    assert all(result.success and result.fact for result in execution.results)
    result = execution.context[aggregate_step.step_id]
    assert result.result_type == "table"
    rows = result.metadata["rows"]
    assert len(rows) == len(expected)
    actual = {row["group"]: row["value"] for row in rows}
    tolerance = 1e-4 if scenario == "area_dependency_chain" else 1e-10
    assert actual == pytest.approx(dict(expected), rel=1e-9, abs=tolerance)
    assert result.data[args["group_by"]].tolist() == [r["group"] for r in rows]
    assert result.data["value"].tolist() == pytest.approx([r["value"] for r in rows])
    assert [r["value"] for r in rows] == sorted(actual.values(), reverse=True)
    maximum = max(expected.values())
    winners = {group for group, value in expected.items() if value == maximum}
    assert rows[0]["group"] in winners
    assert rows[0]["value"] == pytest.approx(maximum, rel=1e-9, abs=tolerance)
    if scenario == "area_dependency_chain":
        area_result = execution.context[area_step.step_id]
        assert isinstance(area_result.data, gpd.GeoDataFrame)
        assert AREA_FIELD in area_result.data.columns
        assert AREA_FIELD not in boundary[1].columns
    print(f"\n测试 4 {scenario} 最高类别：{rows[0]}")


@pytest.mark.asyncio
async def test_05_skill_returns_evidence(live_planner, registry, points, tmp_path):
    """测试 5：真实模型 -> Planner -> 预检 -> Executor -> 工具 -> evidence。"""
    task = make_task("statistics-05", "统计 PM2.5 不同污染等级 pm25_class 的监测点数量，"
                     "返回全部类别及统计结果。", points, "points")
    skill = QuantitativeSkill(live_planner, registry=registry, load_tools=False)
    evidence = await asyncio.wait_for(skill.execute(task), TIMEOUT_SECONDS)
    assert isinstance(evidence, QuantitativeSkillEvidence)
    assert evidence.task_id == task.task_id
    assert evidence.source_skill == "quantitative_skill"
    assert evidence.status == "success" and evidence.error is None
    assert not evidence.warnings
    assert evidence.facts and all(evidence.facts)
    assert all(fact in evidence.summary for fact in evidence.facts)
    operations = evidence.statistics_summary["operations"]
    assert len(operations) == 1 and len(evidence.facts) == 1
    operation = operations[0]
    assert operation["result_type"] == "table"
    if operation["operation"] == "value_counts":
        assert operation["field"] == CLASS_FIELD
        assert operation["top_n"] is None
        key, value = "category", "count"
    else:
        assert operation["operation"] == "group_aggregate"
        assert operation["group_by"] == CLASS_FIELD
        assert operation["aggregate"] == "count"
        assert operation["metric"] in {None, CLASS_FIELD}
        key, value = "group", "value"
    expected = dict(Counter(points[1][CLASS_FIELD]))
    rows = operation["rows"]
    assert len(rows) == len(expected)
    assert {row[key]: row[value] for row in rows} == expected
    assert sum(row[value] for row in rows) == len(points[1])
    assert operation["total_count"] == len(points[1])
    if operation.get("include_ratio"):
        for row in rows:
            assert row["ratio"] == pytest.approx(expected[row[key]] / len(points[1]))
    assert all(category in evidence.summary for category in expected)
    assert evidence.output_refs == []  # 当前表结果在 metadata 中，没有导出文件。

    # 测试函数不 return evidence；检查返回对象后打印并保存，便于手工查看。
    serialized = evidence.model_dump_json(indent=2)
    assert QuantitativeSkillEvidence.model_validate_json(serialized) == evidence
    assert "GeoDataFrame" not in serialized
    payload = json.loads(serialized)
    assert "context" not in payload and "results" not in payload
    artifact = tmp_path / "statistics_evidence.json"
    artifact.write_text(serialized, encoding="utf-8")
    print("\n测试 5 evidence：\n" + serialized)
    print(f"evidence 文件：{artifact}")



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
            "test_05_skill_returns_evidence",
        ]
    )

    # 2. 如果想要【按照名称顺序依次执行所有测试】，并在遇到第一个错误时立即停止调试（加 -x）：
    # pytest.main([__file__, "-v", "-s", "--tb=short", "-x"])
