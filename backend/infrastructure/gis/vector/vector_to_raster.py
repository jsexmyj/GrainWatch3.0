# tool_system/tools/vector_to_raster.py
import os
from pathlib import Path
from typing import Optional, Tuple

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.gis.path_helper import resolve_gis_output_path
from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class VectorToRasterInput(BaseModel):
    """
    矢量转栅格工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(
        ...,
        description="输入的 GeoDataFrame 矢量数据集。调用前须确保其已处于正确的投影坐标系中（栅格分辨率单位取决于该坐标系单位）。",
    )
    value_field: Optional[str] = Field(
        None,
        description="可选，用于写入栅格像元值的属性字段名。为空则对所有要素统一写入 burn_value 指定的固定值。",
    )
    burn_value: float = Field(
        1.0,
        description="当 value_field 为空时，写入所有要素覆盖像元的固定值，默认 1。",
    )
    resolution: Optional[float] = Field(
        None,
        description="输出栅格的像元分辨率（坐标系单位）。与 out_shape 二选一，优先生效。",
    )
    out_shape: Optional[Tuple[int, int]] = Field(
        None,
        description="输出栅格的 (height, width) 像元行列数。当未提供 resolution 时使用该参数推算分辨率。",
    )
    reference_raster: Optional[str] = Field(
        None,
        description="可选，参考栅格文件路径。一旦提供，将自动读取其 crs、transform、width 与 height，"
        "以生成与该参考栅格完全对齐的输出网格，此时 resolution 与 out_shape 均将被忽略。",
    )
    fill: float = Field(
        0.0,
        description="背景（未被要素覆盖的像元）填充值，默认 0。",
    )
    dtype: str = Field(
        "float32",
        description="输出栅格的像元数据类型，默认 'float32'。",
    )
    nodata: Optional[float] = Field(
        None,
        description="可选，输出栅格的 nodata 值。为空则不设置。",
    )
    destination: Optional[str] = Field(
        None,
        description="输出 tif 文件路径。为空时将在默认结果目录下自动生成文件名。",
    )
    file_name: Optional[str] = Field(
        None,
        description="当 destination 为空时使用的文件名（不含后缀），默认自动生成。",
    )


# ==========================================
# 2. 矢量转栅格工具核心类
# ==========================================
class VectorToRasterTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "vector_to_raster"

    # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
    description = (
        "Rasterizes a vector GeoDataFrame into a raster grid, burning either "
        "a fixed value or values from an attribute field into overlapping "
        "pixels. Use this tool when vector features need to be converted "
        "into raster form for grid-based analysis, masking or overlay with "
        "other raster datasets. Returns a FileResource pointing to the "
        "generated GeoTIFF file."
    )

    input_model = VectorToRasterInput
    output_model = ToolResult

    async def execute(self, input_data: VectorToRasterInput) -> ToolResult:
        """
        原子级矢量转栅格核心逻辑：使用 rasterio 依据几何范围栅格化并写出 GeoTIFF
        """
        try:
            import rasterio
            from rasterio.features import rasterize
            from rasterio.transform import from_origin

            gdf = input_data.gdf
            if gdf.empty:
                raise ValueError("输入的 GeoDataFrame 为空，无法栅格化")

            # 若提供了参考栅格，则直接沿用其 crs/transform/width/height 以实现完全对齐
            if input_data.reference_raster:
                ref_path = Path(input_data.reference_raster)
                if not ref_path.exists():
                    raise FileNotFoundError(f"参考栅格文件不存在: {ref_path}")
                with rasterio.open(ref_path) as ref_dataset:
                    transform = ref_dataset.transform
                    width = ref_dataset.width
                    height = ref_dataset.height
                    out_crs = ref_dataset.crs
                # 若矢量与参考栅格坐标系不一致，先重投影矢量以保证几何与网格对齐
                if gdf.crs is not None and out_crs is not None and gdf.crs != out_crs:
                    gdf = gdf.to_crs(out_crs)
            else:
                xmin, ymin, xmax, ymax = gdf.total_bounds
                out_crs = gdf.crs

                # 根据 resolution 或 out_shape 推算输出栅格的行列数与仿射变换
                if input_data.resolution:
                    resolution = input_data.resolution
                    width = max(1, int((xmax - xmin) / resolution + 0.5))
                    height = max(1, int((ymax - ymin) / resolution + 0.5))
                    transform = from_origin(xmin, ymax, resolution, resolution)
                elif input_data.out_shape:
                    height, width = input_data.out_shape
                    res_x = (xmax - xmin) / width
                    res_y = (ymax - ymin) / height
                    transform = from_origin(xmin, ymax, res_x, res_y)
                else:
                    raise ValueError(
                        "必须提供 resolution、out_shape 或 reference_raster 其中之一"
                    )

            # 组装 (几何, 值) 序列供 rasterize 使用
            if input_data.value_field:
                if input_data.value_field not in gdf.columns:
                    raise KeyError(f"字段 '{input_data.value_field}' 不存在于属性表中")
                shapes = list(zip(gdf.geometry, gdf[input_data.value_field]))
            else:
                shapes = list(zip(gdf.geometry, [input_data.burn_value] * len(gdf)))

            burned = rasterize(
                shapes,
                out_shape=(height, width),
                transform=transform,
                fill=input_data.fill,
                dtype=input_data.dtype,
            )

            destination = resolve_gis_output_path(
                destination=input_data.destination,
                file_name=input_data.file_name,
                tool_suffix="vector_to_raster",
            )

            with rasterio.open(
                destination,
                "w",
                driver="GTiff",
                height=height,
                width=width,
                count=1,
                dtype=input_data.dtype,
                crs=out_crs,
                transform=transform,
                nodata=input_data.nodata,
            ) as dst:
                dst.write(burned, 1)

            file_resource = FileResource(
                path=os.path.abspath(destination),
                file_name=os.path.basename(destination),
                format="tif",
                size_bytes=os.path.getsize(destination),
                metadata={
                    "width": width,
                    "height": height,
                    "crs": str(out_crs) if out_crs else None,
                },
            )

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="file",
                data=file_resource,
                metadata={
                    "width": width,
                    "height": height,
                    "value_field": input_data.value_field,
                    "feature_count": int(len(gdf)),
                },
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="file",
                data=None,
                error=f"矢量转栅格失败: {str(e)}",
            )
