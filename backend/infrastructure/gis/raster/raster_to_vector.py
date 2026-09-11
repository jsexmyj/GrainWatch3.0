# tool_system/tools/raster_to_vector.py
from pathlib import Path
from typing import Literal, Optional, Union

import geopandas as gpd
import numpy as np
from pydantic import BaseModel, Field, ConfigDict
from shapely.geometry import shape

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class RasterToVectorInput(BaseModel):
    """
    栅格转矢量工具输入参数模型
    支持传入栅格文件路径，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Union[str, Path] = Field(
        ...,
        description="输入的栅格文件路径（如 .tif），需可被 rasterio 读取。",
    )
    band: int = Field(
        1,
        description="参与矢量化的波段序号（从 1 开始），默认取第 1 波段。",
    )
    nodata: Optional[float] = Field(
        None,
        description="可选，显式指定的无效值，矢量化时将被忽略。为空则使用栅格自身的 nodata 元数据。",
    )
    connectivity: Literal[4, 8] = Field(
        8,
        description="像元连通性，4 表示四邻域，8 表示八邻域，默认 8。",
    )
    simplify_tolerance: Optional[float] = Field(
        None,
        description="可选，输出几何的简化容差（坐标系单位）。为空则不做简化，保留栅格像元边界原始形态。",
    )
    max_unique_values: Optional[int] = Field(
        256,
        description="可选，允许参与矢量化的唯一像元值数量上限，默认 256。若实际唯一值数量超过该阈值将直接报错，"
        "避免对连续型栅格（如 DEM、NDVI）误用本工具生成海量碎小图斑。设为 None 可关闭该项限制。",
    )


# ==========================================
# 2. 栅格转矢量工具核心类
# ==========================================
class RasterToVectorTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "raster_to_vector"

    # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
    description = (
        "Converts a raster dataset into vector polygons, grouping adjacent "
        "pixels that share the same value into individual features. This "
        "tool is intended ONLY for discrete/categorical rasters with a "
        "small number of distinct values (e.g. land-cover classification, "
        "binary masks); it is NOT suitable for continuous rasters such as "
        "DEM or NDVI, which would produce an excessive number of tiny "
        "polygons. Use this tool when classification or mask results need "
        "to be converted into vector features for spatial analysis, "
        "overlay or GIS-standard export. Returns a geopandas.GeoDataFrame "
        "with a 'value' column recording the original pixel value of each "
        "polygon; raises an error if the number of unique pixel values "
        "exceeds max_unique_values."
    )

    input_model = RasterToVectorInput
    output_model = ToolResult

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"栅格转矢量失败: {result.error or '未知错误'}"

        feature_count = result.metadata.get("feature_count", "未知")
        unique_count = result.metadata.get("unique_value_count", "未知")
        return f"已完成栅格矢量化，生成{feature_count}个要素（唯一值数量 {unique_count}）。"

    async def execute(self, input_data: RasterToVectorInput) -> ToolResult:
        """
        原子级栅格转矢量核心逻辑：使用 rasterio 读取栅格并矢量化为多边形
        """
        try:
            import rasterio
            from rasterio.features import shapes as raster_shapes

            path = Path(input_data.source)
            if not path.exists():
                raise FileNotFoundError(f"栅格文件不存在: {path}")

            with rasterio.open(path) as dataset:
                array = dataset.read(input_data.band)

                # 确定无效值掩膜：优先使用显式传入的 nodata
                nodata_value = (
                    input_data.nodata
                    if input_data.nodata is not None
                    else dataset.nodata
                )
                mask_array = array != nodata_value if nodata_value is not None else None

                # 场景限制校验：本工具仅适合离散/分类栅格，唯一值过多时应提前拦截
                valid_values = array[mask_array] if mask_array is not None else array
                unique_count = int(np.unique(valid_values).size)
                if (
                    input_data.max_unique_values is not None
                    and unique_count > input_data.max_unique_values
                ):
                    raise ValueError(
                        f"栅格唯一值数量（{unique_count}）超过阈值（{input_data.max_unique_values}），"
                        "该工具仅适用于离散型/分类栅格，请先对栅格重分类，或调大 max_unique_values 参数"
                    )

                # 逐连通区域矢量化，得到 (几何, 像元值) 序列
                shape_generator = raster_shapes(
                    array,
                    mask=mask_array,
                    connectivity=input_data.connectivity,
                    transform=dataset.transform,
                )

                records = [
                    {"value": value, "geometry": shape(geometry)}
                    for geometry, value in shape_generator
                ]

                result_gdf = gpd.GeoDataFrame(records, crs=dataset.crs)

                # 可选的几何简化，减少后续处理的顶点数量
                if input_data.simplify_tolerance is not None and not result_gdf.empty:
                    result_gdf["geometry"] = result_gdf.geometry.simplify(
                        input_data.simplify_tolerance
                    )

            metadata = {
                "source": str(path),
                "band": input_data.band,
                "unique_value_count": unique_count,
                "feature_count": int(len(result_gdf)),
                "crs": str(result_gdf.crs) if result_gdf.crs else None,
            }

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="vector",
                data=result_gdf,
                metadata=metadata,
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="vector",
                data=None,
                error=f"栅格转矢量失败: {str(e)}",
            )
