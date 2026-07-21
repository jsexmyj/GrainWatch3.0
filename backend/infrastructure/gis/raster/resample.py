# tool_system/tools/resample.py
from pathlib import Path
from typing import Optional, Tuple, Union

from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.gis.path_helper import resolve_gis_output_path
from backend.infrastructure.tool_manager.base import BaseTool, ToolResult

# 支持的重采样方法名称，映射到 rasterio.enums.Resampling 枚举
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
class ResampleInput(BaseModel):
    """
    栅格重采样工具输入参数模型
    支持传入栅格文件路径，需开启任意类型支持

    注意：本工具只在原坐标系内调整像元分辨率/行列数，不涉及坐标系转换。
    若需同时更换坐标系，请使用 raster_reproject 工具。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Union[str, Path] = Field(
        ...,
        description="待重采样的原始栅格文件路径（如 .tif），需可被 rasterio 读取。",
    )
    resolution: Optional[float] = Field(
        None,
        description="目标像元分辨率（与源栅格坐标系单位一致）。与 out_shape 二选一，优先生效。",
    )
    out_shape: Optional[Tuple[int, int]] = Field(
        None,
        description="目标 (height, width) 像元行列数。当未提供 resolution 时使用该参数。",
    )
    resampling: str = Field(
        "nearest",
        description=f"重采样方法，可选 {_RESAMPLING_METHODS} 之一，默认 'nearest'（适合分类/离散数据，连续数据建议使用 'bilinear' 或 'cubic'）。",
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
# 2. 栅格重采样工具核心类
# ==========================================
class ResampleTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "resample"

    # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
    description = (
        "Resamples a raster dataset to a different pixel resolution or "
        "grid shape while keeping its original coordinate reference "
        "system unchanged. Use this tool ONLY when the pixel size/grid "
        "shape needs to be coarsened or refined within the same CRS (e.g. "
        "to match another raster's resolution); it does NOT change the "
        "coordinate system — use the separate 'raster_reproject' tool "
        "when the CRS itself needs to change. Returns a FileResource "
        "pointing to the resampled GeoTIFF file."
    )

    input_model = ResampleInput
    output_model = ToolResult

    async def execute(self, input_data: ResampleInput) -> ToolResult:
        """
        原子级栅格重采样核心逻辑：在同一坐标系下按分辨率或目标行列数重新采样像元
        """
        try:
            import rasterio
            from rasterio.enums import Resampling

            if input_data.resampling not in _RESAMPLING_METHODS:
                raise ValueError(
                    f"不支持的重采样方法 '{input_data.resampling}'，可选值为 {_RESAMPLING_METHODS}"
                )
            resampling_method = getattr(Resampling, input_data.resampling)

            source_path = Path(input_data.source)
            if not source_path.exists():
                raise FileNotFoundError(f"栅格文件不存在: {source_path}")

            with rasterio.open(source_path) as src:
                # 根据 resolution 或 out_shape 推算目标行列数
                if input_data.resolution:
                    new_width = max(
                        1, round(src.width * src.res[0] / input_data.resolution)
                    )
                    new_height = max(
                        1, round(src.height * src.res[1] / input_data.resolution)
                    )
                elif input_data.out_shape:
                    new_height, new_width = input_data.out_shape
                else:
                    raise ValueError("必须提供 resolution 或 out_shape 其中之一")

                # 按目标行列数重新采样各波段像元
                data = src.read(
                    out_shape=(src.count, new_height, new_width),
                    resampling=resampling_method,
                )

                # 依据行列数缩放比例重新计算仿射变换
                new_transform = src.transform * src.transform.scale(
                    (src.width / new_width), (src.height / new_height)
                )

                out_meta = src.meta.copy()
                out_meta.update(
                    {
                        "height": new_height,
                        "width": new_width,
                        "transform": new_transform,
                    }
                )

                destination = resolve_gis_output_path(
                    destination=input_data.destination,
                    file_name=input_data.file_name,
                    source_path=source_path,
                    tool_suffix="resample",
                )

                with rasterio.open(destination, "w", **out_meta) as dst:
                    dst.write(data)

            file_resource = FileResource(
                path=str(Path(destination).resolve()),
                file_name=Path(destination).name,
                format="tif",
                size_bytes=Path(destination).stat().st_size,
                metadata={"width": new_width, "height": new_height},
            )

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="file",
                data=file_resource,
                metadata={
                    "source": str(source_path),
                    "resampling": input_data.resampling,
                    "width": new_width,
                    "height": new_height,
                },
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="file",
                data=None,
                error=f"栅格重采样失败: {str(e)}",
            )
