from typing import Any, Literal

import geopandas as gpd
from pydantic import BaseModel, ConfigDict, Field


from backend.infrastructure.gis.vector.vector_reader_strategy import VECTOR_STRATEGIES
from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


class VectorLoadInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: str = Field(
        ...,
        description="数据源路径。可以是本地文件绝对路径、相对路径，或数据库连接 URI（如 postgresql://user:pwd@host:port/db）",
    )
    source_type:     Literal["auto", "shp", "geojson", "postgres", "postgresql"] = Field(
        default="auto",
        description="数据格式类型。auto 表示根据后缀名或 URI 自动推断。",
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="传递给具体策略读取方法的附加参数（如 PostGIS 的 sql、geom_col 参数，或 shp 的 encoding）。",
    )


class VectorLoadTool(BaseTool):
    name = "vector_load"
    description = (
        "Loads vector data from various sources (Shapefile, GeoJSON, PostGIS) into a GeoDataFrame, "
        "performs format validation and returns metadata."
    )
    input_model = VectorLoadInput
    output_model = ToolResult

    async def execute(self, input_data: VectorLoadInput) -> ToolResult:
        try:
            source = input_data.source
            source_type = _resolve_source_type(
                source=source, source_type=input_data.source_type
            )

            strategy_cls = VECTOR_STRATEGIES.get(source_type)
            if not strategy_cls:
                return ToolResult(
                    success=False,
                    tool_name=self.name,
                    result_type="vector",
                    data=None,
                    error=f"未找到支持的数据源格式或类型: '{source_type}'",
                )

            strategy = strategy_cls()
            gdf: gpd.GeoDataFrame = strategy.read(source, **input_data.extra_params)

            _validate_geodataframe(gdf)

            bounds = gdf.total_bounds
            metadata = {
                "feature_count": len(gdf),
                "geometry_types": sorted(
                    {
                        str(geometry_type)
                        for geometry_type in gdf.geometry.geom_type.dropna().unique()
                    }
                ),
                "crs": str(gdf.crs) if gdf.crs else None,
                "bbox": (
                    [float(value) for value in bounds.tolist()]
                    if len(bounds) == 4
                    else []
                ),
                "source": source,
                "source_type": source_type,
                "geometry_column": gdf.geometry.name,
                "columns": list(gdf.columns),
            }

            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="vector",
                data=gdf,
                metadata=metadata,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="vector",
                data=None,
                error=f"矢量数据加载失败: {str(e)}",
            )


def _resolve_source_type(source: str, source_type: str) -> str:
    if source_type != "auto":
        return "postgres" if source_type == "postgresql" else source_type

    source_text = source.strip().lower()
    if source_text.startswith("postgresql://") or source_text.startswith("postgres://"):
        return "postgres"

    if source_text.endswith(".geojson") or source_text.endswith(".json"):
        return "geojson"

    if source_text.endswith(".shp"):
        return "shp"

    raise ValueError("无法自动识别数据源类型，请显式指定 source_type。")


def _validate_geodataframe(gdf: gpd.GeoDataFrame) -> None:
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise TypeError("策略返回的结果不是 GeoDataFrame。")

    if gdf.empty:
        raise ValueError("读取的数据为空，请检查数据源内容。")

    if gdf.geometry is None:
        raise ValueError("GeoDataFrame 缺少有效的几何列。")

    if gdf.geometry.isna().all():
        raise ValueError("读取的数据缺少有效几何对象。")
