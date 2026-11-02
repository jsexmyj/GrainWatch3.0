import asyncio
import json
import sys
import threading
from copy import deepcopy
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from pydantic import ValidationError
from rasterio.transform import from_origin
from shapely.geometry import LineString, Polygon, box

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agent.base_schemas import BaseSkillTask, DataRef
from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.gis.vector.vector_loader import VectorLoadTool
from backend.infrastructure.tool_manager.base import ToolResult
from backend.infrastructure.tool_manager.loader import ToolLoader
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.skills.base_schemas import ExecutionResult, ToolPlan, ToolStep
from backend.skills.planner_llm_strategy import PlanLLMConfig, PlanLLMStrategy
from backend.skills.quantitative_skill.errors import (
    QuantitativeDataError,
    QuantitativeExecutionError,
)
from backend.skills.quantitative_skill.excutor import QuantitativeExecutor
from backend.skills.quantitative_skill.planner import QuantitativeSkillPlanner
from backend.skills.quantitative_skill.quantitative_skill import QuantitativeSkill
from backend.skills.quantitative_skill.schemas import QuantitativeSkillTask
from backend.utils.paths import PATHS


@pytest.fixture
def registry():
    result = ToolRegistry()
    ToolLoader.auto_load_tools(result, PATHS.infrastructure_path("gis", "statistics"))
    result.register(VectorLoadTool)
    assert (
        len(result.list_tool_catalog(PATHS.infrastructure_path("gis", "statistics")))
        == 8
    )
    return result


@pytest.fixture
def polygons():
    return gpd.GeoDataFrame(
        {"category": ["A", "A", "B"], "yield": [10.0, 20.0, 30.0]},
        geometry=[box(0, 0, 10, 10), box(10, 0, 30, 10), box(0, 10, 10, 40)],
        crs="EPSG:3857",
    )


@pytest.fixture
def vector_files(tmp_path, polygons):
    shp = tmp_path / "plots.shp"
    geojson = tmp_path / "plots.geojson"
    polygons.to_file(shp, driver="ESRI Shapefile", index=False)
    polygons.to_file(geojson, driver="GeoJSON", index=False)
    return {"shp": shp, "geojson": geojson}


@pytest.fixture
def raster_factory(tmp_path):
    def write(name="classes.tif", values=None, crs="EPSG:3857", transform=None):
        path = tmp_path / name
        values = np.array([[1, 1], [2, 0]], dtype="uint8") if values is None else values
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=values.shape[0],
            width=values.shape[1],
            count=1,
            dtype=values.dtype,
            crs=crs,
            nodata=0,
            transform=transform or from_origin(0, 20, 10, 10),
        ) as dataset:
            dataset.write(values, 1)
        return path

    return write


def make_task(path=None, **updates):
    payload = {
        "task_id": "quantitative-test",
        "objective": "统计地块类别和面积",
        "skill_name": "quantitative_skill",
        "parent_agent_task_id": "spatial-agent-test",
    }
    if path is not None:
        payload["input_refs"] = [
            DataRef(
                data_id="plots",
                data_type="vector",
                data_path=str(path),
                data_name=Path(path).name,
            )
        ]
    payload.update(updates)
    return QuantitativeSkillTask(**payload)


def make_plan(*steps):
    return ToolPlan(
        plan_id="statistics-test", steps=[ToolStep(**step) for step in steps]
    )


def make_planner(registry, payload, mode="json", captured=None):
    def invoke(prompt, request):
        if captured is not None:
            captured.append((prompt, request))
        return deepcopy(payload)

    config = PlanLLMConfig(provider="test", mode=mode)
    return QuantitativeSkillPlanner(registry, PlanLLMStrategy(config, invoke))


@pytest.mark.asyncio
@pytest.mark.parametrize("difficulty", ["low", "medium", "high"])
async def test_planner_three_task_difficulties(registry, vector_files, difficulty):
    """低难度分类、中难度多指标、高难度派生指标和混合数据规划。"""
    count = {
        "step_id": "counts",
        "tool_name": "value_counts",
        "arguments": {"gdf": "${inputs.plots}", "field": "category"},
    }
    scenarios = {
        "low": ("统计地块 category 的类别数量和占比。", [count]),
        "medium": (
            "统计类别数量，并分别按类别汇总 yield 均值。",
            [
                count,
                {
                    "step_id": "mean",
                    "tool_name": "group_aggregate",
                    "arguments": {
                        "gdf": "${inputs.plots}",
                        "group_by": "category",
                        "metric": "yield",
                        "aggregate": "mean",
                    },
                },
            ],
        ),
        "high": (
            "计算各类地块总面积、类别数量、范围，并统计分类栅格的像元数。",
            [
                count,
                {
                    "step_id": "area",
                    "tool_name": "vector_area",
                    "arguments": {
                        "gdf": "${inputs.plots}",
                        "area_field_name": "plot_area",
                    },
                },
                {
                    "step_id": "totals",
                    "tool_name": "group_aggregate",
                    "arguments": {
                        "gdf": "${steps.area.data}",
                        "group_by": "category",
                        "metric": "plot_area",
                        "aggregate": "sum",
                    },
                },
                {
                    "step_id": "extent",
                    "tool_name": "bbox",
                    "arguments": {"source": "${inputs.plots}"},
                },
                {
                    "step_id": "pixels",
                    "tool_name": "raster_value_counts",
                    "arguments": {"source": "${inputs.classes}"},
                },
            ],
        ),
    }
    objective, steps = scenarios[difficulty]
    task = make_task(
        vector_files["shp"],
        objective=objective,
        analysis_scope={"difficulty": difficulty},
    )
    if difficulty == "high":
        task.input_refs.append(
            DataRef(
                data_id="classes",
                data_type="raster",
                data_path="classes.tif",
                data_name="classes.tif",
            )
        )
    captured = []
    payload = {"plan_id": f"plan-{difficulty}", "steps": steps}
    if difficulty == "medium":
        payload = {"tool_name": "create_tool_plan", "arguments": payload}
    planner = make_planner(
        registry,
        payload,
        mode="tool_calling" if difficulty == "medium" else "json",
        captured=captured,
    )
    plan = await planner.plan(task)
    prompt, request = captured[0]
    prompt_payload = json.loads(prompt.split("\n", 1)[1])
    assert prompt_payload["task"]["objective"] == objective
    assert "vector_load" not in prompt_payload["tools"]
    assert (
        prompt_payload["tools"]["value_counts"]["input_schema"]["properties"]["gdf"][
            "type"
        ]
        == "string"
    )
    assert "GeoDataFrame" in prompt
    assert request["response_format_schema"]["properties"]["steps"]
    assert [step.tool_name for step in plan.steps] == [
        step["tool_name"] for step in steps
    ]
    assert plan.metadata["execution_mode"] == "dependency_parallel"
    assert len(plan.steps) == {"low": 1, "medium": 2, "high": 5}[difficulty]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step,match",
    [
        (
            {
                "step_id": "bad",
                "tool_name": "vector_load",
                "arguments": {"source": "plots.shp"},
            },
            "不可用",
        ),
        ({"step_id": "bad", "tool_name": "unknown", "arguments": {}}, "不可用"),
        (
            {
                "step_id": "bad",
                "tool_name": "value_counts",
                "arguments": {"gdf": "plots.shp"},
            },
            "missing",
        ),
        (
            {
                "step_id": "bad",
                "tool_name": "bbox",
                "arguments": {"source": "plots.shp", "invented": 1},
            },
            "unknown",
        ),
        (
            {
                "step_id": "bad",
                "tool_name": "bbox",
                "arguments": {"source": "${inputs.missing}"},
            },
            "不存在的输入",
        ),
        (
            {
                "step_id": "bad",
                "tool_name": "bbox",
                "arguments": {"source": "${steps.missing.data}"},
            },
            "不存在的步骤",
        ),
        (
            {
                "step_id": "bad",
                "tool_name": "bbox",
                "arguments": {"source": "${steps.bad.data}"},
            },
            "循环依赖",
        ),
        (
            {
                "step_id": "bad",
                "tool_name": "bbox",
                "arguments": {"source": "${steps.bad.metadata}"},
            },
            "引用格式",
        ),
        (
            {
                "step_id": "bad",
                "tool_name": "bbox",
                "arguments": {"source": "plots.shp"},
                "on_failure": "retry",
            },
            "retry",
        ),
    ],
)
async def test_planner_rejects_invalid_plan(registry, step, match):
    planner = make_planner(registry, {"plan_id": "bad", "steps": [step]})
    with pytest.raises(ValueError, match=match):
        await planner.plan(make_task())


@pytest.mark.asyncio
async def test_planner_respects_allowed_tools_without_mutating_catalog(registry):
    before = deepcopy(
        registry.list_tool_catalog(PATHS.infrastructure_path("gis", "statistics"))
    )
    captured = []
    planner = make_planner(
        registry,
        {
            "plan_id": "allowed",
            "steps": [
                {
                    "step_id": "bbox",
                    "tool_name": "bbox",
                    "arguments": {"source": "plots.shp"},
                }
            ],
        },
        captured=captured,
    )
    await planner.plan(make_task(allowed_tools=["bbox"]))
    assert set(json.loads(captured[0][0].split("\n", 1)[1])["tools"]) == {"bbox"}
    assert (
        registry.list_tool_catalog(PATHS.infrastructure_path("gis", "statistics"))
        == before
    )
    with pytest.raises(ValueError, match="没有可用"):
        await planner.plan(make_task(allowed_tools=["vector_load"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["shp", "geojson"])
async def test_executor_loads_vector_once_and_preserves_input(
    registry, vector_files, format, monkeypatch
):
    original = VectorLoadTool.execute
    sources = []

    async def load(self, input_data):
        sources.append(input_data.source)
        return await original(self, input_data)

    monkeypatch.setattr(VectorLoadTool, "execute", load)
    source = str(vector_files[format])
    plan = make_plan(
        {
            "step_id": "counts",
            "tool_name": "value_counts",
            "arguments": {"gdf": source, "field": "category"},
        },
        {
            "step_id": "bbox",
            "tool_name": "bbox",
            "arguments": {"source": source},
            "output_key": "extent",
        },
    )
    execution = await QuantitativeExecutor(ToolManager(registry)).execute(plan)
    assert len(sources) == 1
    assert execution.success
    assert execution.results[0].metadata["rows"][0] == {
        "category": "A",
        "count": 2,
        "ratio": 2 / 3,
    }
    assert execution.context["extent"] is execution.context["bbox"]
    assert isinstance(plan.steps[0].arguments["gdf"], str)


@pytest.mark.asyncio
async def test_executor_resolves_forward_dependency_and_computes_area_totals(
    registry, polygons
):
    plan = make_plan(
        {
            "step_id": "total",
            "tool_name": "group_aggregate",
            "arguments": {
                "gdf": "${steps.area.data}",
                "group_by": "category",
                "metric": "plot_area",
                "aggregate": "sum",
            },
        },
        {
            "step_id": "area",
            "tool_name": "vector_area",
            "arguments": {"gdf": polygons, "area_field_name": "plot_area"},
        },
        {
            "step_id": "counts",
            "tool_name": "value_counts",
            "arguments": {"gdf": polygons, "field": "category"},
        },
    )
    execution = await QuantitativeExecutor(ToolManager(registry)).execute(plan)
    assert execution.success
    assert execution.results[0].metadata["rows"] == [
        {"group": "A", "value": 300.0},
        {"group": "B", "value": 300.0},
    ]
    assert list(execution.context) == ["total", "area", "counts"]
    assert "plot_area" not in polygons.columns


@pytest.mark.asyncio
async def test_executor_bounds_parallelism(registry, polygons, monkeypatch):
    manager = ToolManager(registry)
    active = 0
    peak = 0
    pair_started = asyncio.Event()

    async def execute(tool_name, **arguments):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            pair_started.set()
        await pair_started.wait()
        await asyncio.sleep(0)
        active -= 1
        return ToolResult(tool_name=tool_name, result_type="bbox", data=None)

    monkeypatch.setattr(manager, "execute", execute)
    plan = make_plan(
        *[
            {
                "step_id": f"step{i}",
                "tool_name": "bbox",
                "arguments": {"source": polygons},
            }
            for i in range(5)
        ]
    )
    result = await asyncio.wait_for(
        QuantitativeExecutor(manager, max_concurrency=2, offload_tools=False).execute(
            plan
        ),
        timeout=5,
    )
    assert result.success
    assert peak == 2


@pytest.mark.asyncio
async def test_executor_offloads_blocking_gis_tools(registry, polygons, monkeypatch):
    """同步计算也应实际并行，而非只并发创建异步任务。"""
    manager = ToolManager(registry)
    barrier = threading.Barrier(2, timeout=5)
    worker_threads = set()

    async def execute(tool_name, **arguments):
        worker_threads.add(threading.get_ident())
        barrier.wait()
        return ToolResult(tool_name=tool_name, result_type="bbox", data=None)

    monkeypatch.setattr(manager, "execute", execute)
    result = await QuantitativeExecutor(manager, max_concurrency=2).execute(
        make_plan(
            {"step_id": "one", "tool_name": "bbox", "arguments": {"source": polygons}},
            {"step_id": "two", "tool_name": "bbox", "arguments": {"source": polygons}},
        )
    )
    assert result.success
    assert len(worker_threads) == 2
    assert threading.get_ident() not in worker_threads


@pytest.mark.asyncio
async def test_executor_fatal_error_cancels_and_drains_siblings(
    registry, polygons, monkeypatch
):
    manager = ToolManager(registry)
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def execute(tool_name, **arguments):
        if arguments.get("area_field_name") == "fatal":
            await sibling_started.wait()
            raise OSError("损坏数据")
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    monkeypatch.setattr(manager, "execute", execute)
    executor = QuantitativeExecutor(manager, offload_tools=False)
    with pytest.raises(QuantitativeDataError, match="损坏数据"):
        await asyncio.wait_for(
            executor.execute(
                make_plan(
                    {
                        "step_id": "fatal",
                        "tool_name": "vector_area",
                        "arguments": {"gdf": polygons, "area_field_name": "fatal"},
                    },
                    {
                        "step_id": "sibling",
                        "tool_name": "bbox",
                        "arguments": {"source": polygons},
                    },
                )
            ),
            timeout=5,
        )
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_case,match",
    [
        ("missing_field", "字段不存在"),
        ("all_null", "全部为空"),
        ("nonnumeric", "数值类型"),
        ("geographic", "地理坐标系"),
        ("no_crs", "CRS"),
        ("empty", "数据为空"),
        ("invalid_geometry", "无效几何"),
        ("wrong_geometry", "几何类型"),
    ],
)
async def test_executor_raises_for_data_requiring_another_skill(
    registry, polygons, data_case, match
):
    data = polygons.copy()
    tool = "vector_area"
    arguments = {"gdf": data}
    if data_case == "missing_field":
        tool, arguments = "value_counts", {"gdf": data, "field": "missing"}
    elif data_case == "all_null":
        data["category"] = None
        tool, arguments = "value_counts", {"gdf": data, "field": "category"}
    elif data_case == "nonnumeric":
        tool, arguments = "group_aggregate", {
            "gdf": data,
            "group_by": "category",
            "metric": "category",
            "aggregate": "sum",
        }
    elif data_case == "geographic":
        arguments["gdf"] = data.to_crs("EPSG:4326")
    elif data_case == "no_crs":
        arguments["gdf"] = gpd.GeoDataFrame(
            data.drop(columns="geometry"), geometry=list(data.geometry)
        )
    elif data_case == "empty":
        arguments["gdf"] = data.iloc[0:0]
    elif data_case == "invalid_geometry":
        data.loc[0, "geometry"] = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
    elif data_case == "wrong_geometry":
        arguments["gdf"] = gpd.GeoDataFrame(
            geometry=[LineString([(0, 0), (3, 4)])], crs="EPSG:3857"
        )
    plan = make_plan(
        {
            "step_id": "bad-data",
            "tool_name": tool,
            "arguments": arguments,
            "on_failure": "continue",
        }
    )
    with pytest.raises(QuantitativeDataError, match=match) as error:
        await QuantitativeExecutor(ToolManager(registry)).execute(plan)
    assert error.value.step_id == "bad-data"
    assert error.value.category == "data"


@pytest.mark.asyncio
async def test_executor_missing_values_and_geometry_continue_with_warnings(
    registry, polygons
):
    data = polygons.copy()
    data.loc[0, "category"] = None
    data.loc[1, "geometry"] = None
    result = await QuantitativeExecutor(ToolManager(registry)).execute(
        make_plan(
            {
                "step_id": "counts",
                "tool_name": "value_counts",
                "arguments": {"gdf": data, "field": "category"},
            },
            {"step_id": "area", "tool_name": "vector_area", "arguments": {"gdf": data}},
        )
    )
    assert result.success
    assert result.results[0].metadata["denominator_count"] == 2
    assert result.results[1].data["area"].sum() == 400
    assert any("缺失值" in message for message in result.warnings)
    assert any("空几何" in message for message in result.warnings)
    assert len(data) == 3


@pytest.mark.asyncio
async def test_executor_all_null_category_can_be_counted_explicitly(registry, polygons):
    polygons["category"] = None
    execution = await QuantitativeExecutor(ToolManager(registry)).execute(
        make_plan(
            {
                "step_id": "nulls",
                "tool_name": "value_counts",
                "arguments": {"gdf": polygons, "field": "category", "dropna": False},
            },
        )
    )
    assert execution.success
    assert execution.results[0].metadata["rows"] == [
        {"category": None, "count": 3, "ratio": 1.0}
    ]
    assert execution.warnings


@pytest.mark.asyncio
async def test_executor_length_supports_custom_geometry_column(registry):
    lines = gpd.GeoDataFrame(
        geometry=[LineString([(0, 0), (3, 4)])], crs="EPSG:3857"
    ).rename_geometry("shape")
    execution = await QuantitativeExecutor(ToolManager(registry)).execute(
        make_plan(
            {
                "step_id": "length",
                "tool_name": "vector_length",
                "arguments": {"gdf": lines},
            },
        )
    )
    assert execution.results[0].data["length"].tolist() == [5.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["shp", "tif"])
async def test_executor_corrupt_file_always_raises(registry, tmp_path, suffix):
    path = tmp_path / f"corrupt.{suffix}"
    path.write_bytes(b"not a valid GIS file")
    arguments = {"gdf": str(path)} if suffix == "shp" else {"source": str(path)}
    tool = "vector_area" if suffix == "shp" else "raster_value_counts"
    with pytest.raises(QuantitativeDataError):
        await QuantitativeExecutor(ToolManager(registry)).execute(
            make_plan(
                {
                    "step_id": "corrupt",
                    "tool_name": tool,
                    "arguments": arguments,
                    "on_failure": "warn",
                },
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "data_result", "unknown_result"])
async def test_executor_does_not_hide_fatal_tool_failure(
    registry, polygons, monkeypatch, failure
):
    manager = ToolManager(registry)

    async def execute(tool_name, **arguments):
        if failure == "exception":
            raise OSError("文件损坏")
        return ToolResult(
            success=False,
            tool_name=tool_name,
            result_type="bbox",
            data=None,
            error="文件损坏",
            metadata={"failure_category": "data"} if failure == "data_result" else {},
        )

    monkeypatch.setattr(manager, "execute", execute)
    with pytest.raises(QuantitativeExecutionError, match="文件损坏"):
        await QuantitativeExecutor(manager, offload_tools=False).execute(
            make_plan(
                {
                    "step_id": "failure",
                    "tool_name": "bbox",
                    "arguments": {"source": polygons},
                    "on_failure": "continue",
                },
            )
        )


@pytest.mark.asyncio
async def test_executor_recoverable_failure_skips_dependents_and_keeps_independent_results(
    registry, polygons, monkeypatch
):
    manager = ToolManager(registry)
    calls = []

    async def execute(tool_name, **arguments):
        calls.append(tool_name)
        return ToolResult(
            success=tool_name != "vector_area",
            tool_name=tool_name,
            result_type="vector" if tool_name == "vector_area" else "bbox",
            data=None,
            error="可选统计暂不可用" if tool_name == "vector_area" else None,
            metadata=(
                {"failure_category": "recoverable"}
                if tool_name == "vector_area"
                else {}
            ),
        )

    monkeypatch.setattr(manager, "execute", execute)
    result = await QuantitativeExecutor(manager, offload_tools=False).execute(
        make_plan(
            {
                "step_id": "optional",
                "tool_name": "vector_area",
                "arguments": {"gdf": polygons},
                "on_failure": "warn",
            },
            {
                "step_id": "dependent",
                "tool_name": "group_aggregate",
                "arguments": {
                    "gdf": "${steps.optional.data}",
                    "group_by": "category",
                    "metric": "area",
                    "aggregate": "sum",
                },
            },
            {
                "step_id": "independent",
                "tool_name": "bbox",
                "arguments": {"source": polygons},
            },
        )
    )
    assert not result.success
    assert result.failed_step_id == "optional"
    assert result.results[1].result_type == "skipped"
    assert result.results[2].success
    assert "group_aggregate" not in calls
    assert len(result.warnings) == 2


@pytest.mark.asyncio
async def test_executor_raster_keeps_path_and_reports_nodata(registry, raster_factory):
    path = raster_factory()
    execution = await QuantitativeExecutor(ToolManager(registry)).execute(
        make_plan(
            {
                "step_id": "pixels",
                "tool_name": "raster_value_counts",
                "arguments": {"source": str(path)},
            },
            {
                "step_id": "area",
                "tool_name": "raster_class_area",
                "arguments": {"source": str(path)},
            },
            {
                "step_id": "description",
                "tool_name": "describe_raster",
                "arguments": {"source": str(path), "compute_stats": True},
            },
            {
                "step_id": "bbox",
                "tool_name": "bbox",
                "arguments": {"source": str(path)},
            },
        )
    )
    assert execution.success
    assert execution.results[0].metadata["total_pixels"] == 3
    assert execution.results[1].metadata["total_area"] == 300.0
    assert execution.results[2].metadata["band_statistics"]["band_1"][
        "mean"
    ] == pytest.approx(4 / 3)
    assert sum("NoData" in message for message in execution.warnings) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,match",
    [
        ("all_nodata", "全为 NoData"),
        ("geographic", "投影 CRS"),
        ("band", "波段"),
        ("rotated", "非旋转"),
    ],
)
async def test_executor_raster_validation(registry, raster_factory, case, match):
    from affine import Affine

    path = raster_factory(
        values=np.zeros((2, 2), dtype="uint8") if case == "all_nodata" else None,
        crs="EPSG:4326" if case == "geographic" else "EPSG:3857",
        transform=Affine(10, 1, 0, 0, -10, 20) if case == "rotated" else None,
    )
    arguments = {"source": str(path), "band": 2 if case == "band" else 1}
    with pytest.raises(QuantitativeExecutionError, match=match):
        await QuantitativeExecutor(ToolManager(registry)).execute(
            make_plan(
                {
                    "step_id": "invalid",
                    "tool_name": "raster_class_area",
                    "arguments": arguments,
                },
            )
        )


@pytest.mark.asyncio
async def test_executor_all_nodata_can_be_counted_explicitly(registry, raster_factory):
    path = raster_factory(values=np.zeros((2, 2), dtype="uint8"))
    result = await QuantitativeExecutor(ToolManager(registry)).execute(
        make_plan(
            {
                "step_id": "nodata",
                "tool_name": "raster_value_counts",
                "arguments": {"source": str(path), "include_nodata": True},
            },
        )
    )
    assert result.success
    assert result.results[0].metadata["total_pixels"] == 4
    assert result.warnings


@pytest.mark.asyncio
async def test_skill_validates_task_and_file_before_planning(registry, tmp_path):
    captured = []
    skill = QuantitativeSkill(
        make_planner(registry, {}, captured=captured), load_tools=False
    )
    task = make_task(tmp_path / "missing.shp")
    with pytest.raises(QuantitativeDataError, match="文件不存在"):
        await skill.execute(task)
    with pytest.raises(ValidationError):
        await skill.execute(task.model_copy(update={"objective": " "}))
    assert captured == []


@pytest.mark.asyncio
async def test_skill_prevalidates_all_tool_data_before_any_execution(
    registry, vector_files, monkeypatch
):
    plan = {
        "plan_id": "precheck",
        "steps": [
            {
                "step_id": "good",
                "tool_name": "bbox",
                "arguments": {"source": "${inputs.plots}"},
            },
            {
                "step_id": "bad",
                "tool_name": "value_counts",
                "arguments": {"gdf": "${inputs.plots}", "field": "absent"},
            },
        ],
    }
    calls = []
    manager = ToolManager(registry)

    async def execute(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(manager, "execute", execute)
    skill = QuantitativeSkill(
        make_planner(registry, plan),
        executor=QuantitativeExecutor(manager),
        load_tools=False,
    )
    with pytest.raises(QuantitativeDataError, match="字段不存在"):
        await skill.execute(make_task(vector_files["shp"]))
    assert calls == []


@pytest.mark.asyncio
async def test_skill_end_to_end_real_shapefile_and_raster(
    registry, vector_files, raster_factory
):
    raster_path = raster_factory()
    task = make_task(vector_files["shp"])
    task.input_refs.append(
        DataRef(
            data_id="classes",
            data_type="raster",
            data_path=str(raster_path),
            data_name=raster_path.name,
        )
    )
    payload = {
        "plan_id": "e2e",
        "steps": [
            {
                "step_id": "area",
                "tool_name": "vector_area",
                "arguments": {"gdf": "${inputs.plots}"},
            },
            {
                "step_id": "totals",
                "tool_name": "group_aggregate",
                "arguments": {
                    "gdf": "${steps.area.data}",
                    "group_by": "category",
                    "metric": "area",
                    "aggregate": "sum",
                },
            },
            {
                "step_id": "counts",
                "tool_name": "value_counts",
                "arguments": {"gdf": "${inputs.plots}", "field": "category"},
            },
            {
                "step_id": "pixels",
                "tool_name": "raster_value_counts",
                "arguments": {"source": "${inputs.classes}"},
            },
        ],
    }
    skill = QuantitativeSkill(make_planner(registry, payload), load_tools=False)
    evidence = await skill.execute(BaseSkillTask.model_validate(task.model_dump()))
    assert evidence.status == "success"
    assert evidence.source_skill == "quantitative_skill"
    assert len(evidence.facts) == 4
    assert "总面积为600" in evidence.summary
    assert evidence.statistics_summary["operations"][1]["rows"] == [
        {"group": "A", "value": 300.0},
        {"group": "B", "value": 300.0},
    ]
    assert evidence.warnings and "NoData" in evidence.warnings[0]
    assert "GeoDataFrame" not in evidence.model_dump_json()


def test_evidence_partial_status_and_file_references():
    task = make_task()
    evidence = QuantitativeSkill.build_evidence(
        task,
        ExecutionResult(
            success=False,
            failed_step_id="optional",
            warnings=["可选工具失败"],
            results=[
                ToolResult(
                    tool_name="export",
                    result_type="file",
                    data=FileResource(
                        path="data/result.csv", file_name="result.csv", format="csv"
                    ),
                    fact="已导出统计表。",
                ),
                ToolResult(
                    success=False,
                    tool_name="optional",
                    result_type="table",
                    data=None,
                    error="可选工具失败",
                ),
            ],
        ),
    )
    assert evidence.status == "partial"
    assert evidence.output_refs[0].data_path == "data/result.csv"
    assert evidence.output_refs[0].metadata["format"] == "csv"
    assert evidence.facts == ["已导出统计表。"]
    assert evidence.error == "可选工具失败"
    assert evidence.warnings == ["可选工具失败"]


def test_task_rejects_duplicate_input_ids_and_wrong_skill():
    task = make_task("plots.shp")
    with pytest.raises(ValidationError, match="必须唯一"):
        make_task(input_refs=task.input_refs * 2)
    with pytest.raises(ValidationError, match="quantitative_skill"):
        make_task(skill_name="spatial_skill")
    with pytest.raises(ValidationError, match="dataset_paths"):
        make_task(analysis_scope={"dataset_paths": ["plots.shp"]})


def test_skill_registry_consistency_and_default_loading():
    registry = ToolRegistry()
    planner = make_planner(registry, {})
    skill = QuantitativeSkill(planner)
    assert skill.registry is planner.registry
    assert "raster_class_area" in registry.list_all_tools()
    with pytest.raises(ValueError, match="同一个"):
        QuantitativeSkill(planner, registry=ToolRegistry())


@pytest.mark.asyncio
async def test_executor_rejects_cycles_and_context_key_collisions(registry):
    executor = QuantitativeExecutor(ToolManager(registry))
    plans = [
        make_plan(
            {
                "step_id": "a",
                "tool_name": "bbox",
                "arguments": {"source": "${steps.b.data}"},
            },
            {
                "step_id": "b",
                "tool_name": "bbox",
                "arguments": {"source": "${steps.a.data}"},
            },
        ),
        make_plan(
            {
                "step_id": "a",
                "tool_name": "bbox",
                "arguments": {"source": "a.tif"},
                "output_key": "b",
            },
            {"step_id": "b", "tool_name": "bbox", "arguments": {"source": "b.tif"}},
        ),
    ]
    for plan in plans:
        with pytest.raises(ValueError):
            await executor.execute(plan)
