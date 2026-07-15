import os
from datetime import datetime
from typing import Any, Literal

import geopandas as gpd
from pydantic import BaseModel, ConfigDict, Field

from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.gis.vector.vector_writer_strategy import (
    VECTOR_WRITER_STRATEGIES,
)
from backend.infrastructure.tool_manager.base import BaseTool, ToolResult
from backend.utils.paths import PATHS

# 各导出格式对应的文件后缀
_SUFFIX_MAP: dict[str, str] = {
    "shp": ".shp",
    "geojson": ".geojson",
    "gpkg": ".gpkg",
}


class VectorWriteInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(
        ...,
        description="待导出的矢量数据（GeoDataFrame），通常来自上游工具产生的中间结果。",
    )
    destination: str | None = Field(
        default=None,
        description="导出目标文件路径。为空时将根据 target_type 在结果目录下自动生成路径。",
    )
    target_type: Literal["auto", "shp", "geojson", "gpkg"] = Field(
        default="auto",
        description="导出文件格式。auto 表示根据 destination 的后缀名自动推断；"
        "若未提供 destination，则必须显式指定该字段。",
    )
    file_name: str | None = Field(
        default=None,
        description="当 destination 为空时使用的文件名（不含后缀），默认自动生成带时间戳的文件名。",
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="传递给具体写出策略的附加参数（如 Shapefile 的 encoding、GeoPackage 的 layer）。",
    )


class VectorWriteTool(BaseTool):
    name = "vector_write"
    description = (
        "Exports an in-memory GeoDataFrame (produced by upstream GIS tools) into a persisted "
        "vector file such as Shapefile, GeoJSON or GeoPackage, and returns a FileResource "
    )
    input_model = VectorWriteInput
    output_model = ToolResult

    async def execute(self, input_data: VectorWriteInput) -> ToolResult:
        try:
            gdf = input_data.gdf
            _validate_geodataframe(gdf)

            target_type = _resolve_target_type(
                destination=input_data.destination, target_type=input_data.target_type
            )

            strategy_cls = VECTOR_WRITER_STRATEGIES.get(target_type)
            if not strategy_cls:
                return ToolResult(
                    success=False,
                    tool_name=self.name,
                    result_type="file",
                    data=None,
                    error=f"未找到支持的导出格式: '{target_type}'",
                )

            destination = _resolve_destination(
                destination=input_data.destination,
                target_type=target_type,
                file_name=input_data.file_name,
            )

            strategy = strategy_cls()
            actual_path = strategy.write(gdf, destination, **input_data.extra_params)

            file_resource = _build_file_resource(actual_path, target_type, gdf)

            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="file",
                data=file_resource,
                metadata={
                    "feature_count": len(gdf),
                    "target_type": target_type,
                },
            )

        except Exception as e:
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="file",
                data=None,
                error=f"矢量数据导出失败: {str(e)}",
            )


def _resolve_target_type(destination: str | None, target_type: str) -> str:
    """根据显式参数或 destination 后缀名推断导出格式"""
    if target_type != "auto":
        return target_type

    if not destination:
        raise ValueError("target_type 为 auto 时必须提供 destination 以推断导出格式。")

    suffix = destination.strip().lower()
    if suffix.endswith(".shp"):
        return "shp"
    if suffix.endswith(".geojson") or suffix.endswith(".json"):
        return "geojson"
    if suffix.endswith(".gpkg"):
        return "gpkg"

    raise ValueError(
        "无法根据 destination 后缀名自动识别导出格式，请显式指定 target_type。"
    )


def _resolve_destination(
    destination: str | None, target_type: str, file_name: str | None
) -> str:
    """确定实际写出路径；若未提供 destination，则在默认结果目录下自动生成"""
    if destination:
        # 确保父目录存在，避免驱动因目录缺失而写出失败
        parent_dir = os.path.dirname(destination)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        return destination

    suffix = _SUFFIX_MAP[target_type]
    if file_name:
        name = f"{file_name}{suffix}"
    else:
        name = f"vector_output"

    output_dir = PATHS.data_path("运行结果")
    return str(output_dir / name)


def _build_file_resource(
    path: str, target_type: str, gdf: gpd.GeoDataFrame
) -> FileResource:
    """构建统一的文件资源描述对象，供下游 Agent/Skill 消费"""
    size_bytes = os.path.getsize(path) if os.path.exists(path) else 0
    bounds = gdf.total_bounds

    return FileResource(
        path=os.path.abspath(path),
        file_name=os.path.basename(path),
        format=target_type,
        size_bytes=size_bytes,
        metadata={
            "feature_count": len(gdf),
            "geometry_types": sorted(
                {
                    str(geometry_type)
                    for geometry_type in gdf.geometry.geom_type.dropna().unique()
                }
            ),
            "crs": str(gdf.crs) if gdf.crs else None,
            "bbox": (
                [float(value) for value in bounds.tolist()] if len(bounds) == 4 else []
            ),
        },
    )


def _validate_geodataframe(gdf: gpd.GeoDataFrame) -> None:
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise TypeError("输入数据不是 GeoDataFrame。")

    if gdf.empty:
        raise ValueError("待导出的数据为空，请检查上游数据内容。")

    if gdf.geometry is None or gdf.geometry.isna().all():
        raise ValueError("待导出的数据缺少有效几何对象。")
