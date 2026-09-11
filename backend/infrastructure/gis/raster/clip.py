# tool_system/tools/clip.py
import os
from pathlib import Path
from typing import Optional, Union

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict
from shapely.geometry import box

from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.gis.path_helper import resolve_gis_output_path
from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class RasterClipInput(BaseModel):
    """
    影像裁剪工具输入参数模型
    支持传入栅格文件路径，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Union[str, Path] = Field(
        ...,
        description="待裁剪的原始栅格文件路径（如 .tif），需可被 rasterio 读取。",
    )
    clip_source: Union[gpd.GeoDataFrame, str, Path] = Field(
        ...,
        description="裁剪范围来源，支持两种形式：geopandas.GeoDataFrame（使用其几何作为裁剪边界），"
        "或另一个栅格文件路径（使用其外包矩形范围作为裁剪边界）。",
    )
    crop: bool = Field(
        True,
        description="是否将输出栅格裁切到裁剪几何的外包范围，默认为 True（裁掉多余的空白像元）。",
    )
    all_touched: bool = Field(
        False,
        description="是否将所有与裁剪几何相交（即使只是边缘接触）的像元都计入结果，默认 False（仅计入像元中心落入范围内的像元）。",
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
# 2. 影像裁剪工具核心类
# ==========================================
class RasterClipTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "raster_clip"

    # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
    description = (
        "Clips a raster dataset using either a vector GeoDataFrame's "
        "geometries or another raster's bounding extent as the clip "
        "boundary. Use this tool when a study-area boundary or a reference "
        "raster's coverage needs to be applied to crop a source raster to "
        "the region of interest. Returns a FileResource pointing to the "
        "clipped GeoTIFF file."
    )

    input_model = RasterClipInput
    output_model = ToolResult

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"影像裁剪失败: {result.error or '未知错误'}"

        width = result.metadata.get("width", "未知")
        height = result.metadata.get("height", "未知")
        file_name = getattr(result.data, "file_name", "输出影像")
        return f"已完成影像裁剪，生成{file_name}，尺寸为{width}x{height}。"

    async def execute(self, input_data: RasterClipInput) -> ToolResult:
        """
        原子级影像裁剪核心逻辑：使用 rasterio.mask 依据矢量或栅格范围裁剪原始影像
        """
        try:
            import rasterio
            from rasterio.mask import mask as rasterio_mask

            source_path = Path(input_data.source)
            if not source_path.exists():
                raise FileNotFoundError(f"栅格文件不存在: {source_path}")

            # 解析裁剪几何：GeoDataFrame 直接取其要素几何，栅格则取其外包矩形
            clip_source = input_data.clip_source
            if isinstance(clip_source, gpd.GeoDataFrame):
                if clip_source.empty:
                    raise ValueError("裁剪用 GeoDataFrame 为空")
                geometries = list(clip_source.geometry)
            else:
                clip_path = Path(clip_source)
                if not clip_path.exists():
                    raise FileNotFoundError(f"裁剪参考栅格文件不存在: {clip_path}")
                with rasterio.open(clip_path) as clip_dataset:
                    geometries = [box(*clip_dataset.bounds)]

            with rasterio.open(source_path) as dataset:
                out_image, out_transform = rasterio_mask(
                    dataset,
                    geometries,
                    crop=input_data.crop,
                    all_touched=input_data.all_touched,
                )
                out_meta = dataset.meta.copy()
                out_meta.update(
                    {
                        "height": out_image.shape[1],
                        "width": out_image.shape[2],
                        "transform": out_transform,
                    }
                )

            destination = resolve_gis_output_path(
                destination=input_data.destination,
                file_name=input_data.file_name,
                source_path=source_path,
                tool_suffix="clip",
            )

            with rasterio.open(destination, "w", **out_meta) as dst:
                dst.write(out_image)

            file_resource = FileResource(
                path=os.path.abspath(destination),
                file_name=os.path.basename(destination),
                format="tif",
                size_bytes=os.path.getsize(destination),
                metadata={
                    "width": out_meta["width"],
                    "height": out_meta["height"],
                    "crs": str(out_meta.get("crs")) if out_meta.get("crs") else None,
                },
            )

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="file",
                data=file_resource,
                metadata={
                    "source": str(source_path),
                    "width": out_meta["width"],
                    "height": out_meta["height"],
                },
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="file",
                data=None,
                error=f"影像裁剪失败: {str(e)}",
            )
