# tool_system/tools/describe_raster.py
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class DescribeRasterInput(BaseModel):
    """
    栅格元信息读取工具输入参数模型
    支持传入栅格文件路径，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Union[str, Path] = Field(
        ...,
        description="输入的栅格文件路径（如 .tif），需可被 rasterio 读取。",
    )
    compute_stats: bool = Field(
        False,
        description="是否计算各波段的统计信息（Min/Max/Mean/StdDev），默认 False（仅读取元信息，速度更快）。",
    )
    band: Optional[int] = Field(
        None,
        description="可选，仅计算指定波段（从 1 开始）的统计信息。为空且 compute_stats 为 True 时，计算所有波段。",
    )


# ==========================================
# 2. 栅格元信息读取工具核心类
# ==========================================
class DescribeRasterTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "describe_raster"

    # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
    description = (
        "Reads the metadata of a raster dataset, including basic info "
        "(width, height, band count, dtype, nodata), geographic info "
        "(CRS, bounding box, affine transform), and optionally per-band "
        "statistics (min/max/mean/stddev). Use this tool when a quick "
        "overview or pre-check of a raster's structure and geospatial "
        "properties is needed before further processing. Returns the "
        "metadata as ToolResult.metadata without modifying the raster."
    )

    input_model = DescribeRasterInput
    output_model = ToolResult

    async def execute(self, input_data: DescribeRasterInput) -> ToolResult:
        """
        原子级栅格元信息读取核心逻辑：使用 rasterio 读取基础信息、地理信息及可选统计信息
        """
        try:
            import rasterio

            path = Path(input_data.source)
            if not path.exists():
                raise FileNotFoundError(f"栅格文件不存在: {path}")

            with rasterio.open(path) as dataset:
                bounds = dataset.bounds

                # 基础信息 + 地理信息
                metadata = {
                    "width": int(dataset.width),
                    "height": int(dataset.height),
                    "band_count": int(dataset.count),
                    "dtype": dataset.dtypes[0] if dataset.dtypes else None,
                    "dtypes": list(dataset.dtypes),
                    "nodata": dataset.nodata,
                    "crs": str(dataset.crs) if dataset.crs else None,
                    "bbox": {
                        "xmin": float(bounds.left),
                        "ymin": float(bounds.bottom),
                        "xmax": float(bounds.right),
                        "ymax": float(bounds.top),
                    },
                    "transform": tuple(dataset.transform)[:6],
                }

                # 可选的逐波段统计信息
                if input_data.compute_stats:
                    band_indices = (
                        [input_data.band]
                        if input_data.band
                        else range(1, dataset.count + 1)
                    )
                    band_statistics = {}
                    for band_index in band_indices:
                        band_array = dataset.read(band_index, masked=True)
                        band_statistics[f"band_{band_index}"] = {
                            "min": float(band_array.min()),
                            "max": float(band_array.max()),
                            "mean": float(band_array.mean()),
                            "std": float(band_array.std()),
                        }
                    metadata["band_statistics"] = band_statistics

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="metadata",  # 结果为元信息描述，非矢量/栅格数据本身
                data=None,
                metadata=metadata,
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="metadata",
                data=None,
                error=f"栅格元信息读取失败: {str(e)}",
            )
