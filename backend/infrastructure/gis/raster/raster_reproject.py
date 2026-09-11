# tool_system/tools/raster_reproject.py
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.gis.path_helper import resolve_gis_output_path
from backend.infrastructure.tool_manager.base import BaseTool, ToolResult

# 支持的重采样方法名称，映射到 rasterio.warp.Resampling 枚举
_RESAMPLING_METHODS = (
    "nearest",
    "bilinear",
    "cubic",
    "cubic_spline",
    "lanczos",
    "average",
    "mode",
)


# ==========================================
# 1. 输入模型定义
# ==========================================
class RasterReprojectInput(BaseModel):
    """
    栅格重投影工具输入参数模型
    支持传入栅格文件路径，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Union[str, Path] = Field(
        ...,
        description="待重投影的原始栅格文件路径（如 .tif），需可被 rasterio 读取。",
    )
    target_crs: Union[str, int] = Field(
        ...,
        description="目标坐标系，支持 EPSG 码（如 4326、32650）或 PROJ/WKT 字符串（如 'EPSG:4326'）。",
    )
    resampling: str = Field(
        "nearest",
        description=(
            f"重投影过程中的重采样/插值方法，因坐标系变换必然伴随像元重采样，"
            f"可选 {_RESAMPLING_METHODS} 之一，默认 'nearest'（适合分类/离散数据，"
            "连续数据建议使用 'bilinear' 或 'cubic'）。若需在不改变坐标系的前提下"
            "仅调整分辨率，请使用 resample 工具。"
        ),
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
# 2. 栅格重投影工具核心类
# ==========================================
class RasterReprojectTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "raster_reproject"

    # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
    description = (
        "Reprojects a raster dataset from its source coordinate reference "
        "system to a target CRS, using rasterio's automatically computed "
        "output resolution for the new CRS. Use this tool ONLY when the "
        "coordinate system itself needs to change (e.g. to align with "
        "other datasets in a different CRS, or before area/distance-based "
        "analysis); it does NOT let you independently control pixel "
        "resolution — use the separate 'resample' tool when only the "
        "pixel size/grid shape needs to be adjusted within the same CRS. "
        "Returns a FileResource pointing to the reprojected GeoTIFF file."
    )

    input_model = RasterReprojectInput
    output_model = ToolResult

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"栅格重投影失败: {result.error or '未知错误'}"

        source = result.metadata.get("source", "未知来源")
        target = result.metadata.get("target_crs", "未知坐标系")
        resampling = result.metadata.get("resampling", "nearest")
        return f"已将栅格从{source}重投影到{target}（重采样方法：{resampling}）。"

    async def execute(self, input_data: RasterReprojectInput) -> ToolResult:
        """
        原子级栅格重投影核心逻辑：使用 rasterio.warp 计算目标网格并逐波段重投影
        """
        try:
            import rasterio
            from rasterio.warp import calculate_default_transform, reproject, Resampling

            if input_data.resampling not in _RESAMPLING_METHODS:
                raise ValueError(
                    f"不支持的重采样方法 '{input_data.resampling}'，可选值为 {_RESAMPLING_METHODS}"
                )
            resampling_method = getattr(Resampling, input_data.resampling)

            source_path = Path(input_data.source)
            if not source_path.exists():
                raise FileNotFoundError(f"栅格文件不存在: {source_path}")

            with rasterio.open(source_path) as src:
                dst_crs = input_data.target_crs
                # 不传入 resolution，完全交由 rasterio 根据目标坐标系自动推算默认分辨率，
                # 确保本工具仅负责坐标系转换，不介入分辨率调整职责
                transform, width, height = calculate_default_transform(
                    src.crs,
                    dst_crs,
                    src.width,
                    src.height,
                    *src.bounds,
                )

                out_meta = src.meta.copy()
                out_meta.update(
                    {
                        "crs": dst_crs,
                        "transform": transform,
                        "width": width,
                        "height": height,
                    }
                )

                destination = resolve_gis_output_path(
                    destination=input_data.destination,
                    file_name=input_data.file_name,
                    source_path=source_path,
                    tool_suffix="raster_reproject",
                )

                with rasterio.open(destination, "w", **out_meta) as dst:
                    # 逐波段执行重投影，保持原始波段数量与数据类型
                    for band_index in range(1, src.count + 1):
                        reproject(
                            source=rasterio.band(src, band_index),
                            destination=rasterio.band(dst, band_index),
                            src_transform=src.transform,
                            src_crs=src.crs,
                            dst_transform=transform,
                            dst_crs=dst_crs,
                            resampling=resampling_method,
                        )

            file_resource = FileResource(
                path=str(Path(destination).resolve()),
                file_name=Path(destination).name,
                format="tif",
                size_bytes=Path(destination).stat().st_size,
                metadata={
                    "width": width,
                    "height": height,
                    "crs": str(dst_crs),
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
                    "target_crs": str(dst_crs),
                    "resampling": input_data.resampling,
                    "width": width,
                    "height": height,
                },
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="file",
                data=None,
                error=f"栅格重投影失败: {str(e)}",
            )
