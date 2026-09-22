# tool_system/tools/describe_raster.py
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult
from backend.utils.paths import resolve_file_path


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

    @staticmethod
    def _format_number(value: Any) -> str:
        if isinstance(value, (int,)):
            return str(value)
        if isinstance(value, float):
            text = f"{value:.6f}".rstrip("0").rstrip(".")
            return text if text else "0"
        return str(value)

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"栅格元信息读取失败: {result.error or '未知错误'}"

        data = result.data
        if not isinstance(data, dict):
            return "栅格元信息结果为空。"

        width = data.get("width")
        height = data.get("height")
        band_count = data.get("band_count")
        if width is None or height is None or band_count is None:
            return "栅格关键元信息缺失。"

        pixel_total = int(width) * int(height)
        crs = data.get("crs") or "未知坐标系"
        base_fact = (
            f"栅格尺寸为{width}x{height}，像元总数为{pixel_total}，"
            f"波段数为{band_count}，坐标系为{crs}。"
        )

        band_statistics = data.get("band_statistics")
        if isinstance(band_statistics, dict) and band_statistics:
            first_key = next(iter(band_statistics))
            first_stats = band_statistics.get(first_key, {})
            if isinstance(first_stats, dict):
                min_value = self._format_number(first_stats.get("min", "未知"))
                max_value = self._format_number(first_stats.get("max", "未知"))
                return (
                    f"{base_fact}{first_key}最小值为{min_value}，"
                    f"最大值为{max_value}。"
                )

        return base_fact

    async def execute(self, input_data: DescribeRasterInput) -> ToolResult:
        """
        原子级栅格元信息读取核心逻辑：使用 rasterio 读取基础信息、地理信息及可选统计信息
        """
        try:
            import rasterio

            path = resolve_file_path(str(input_data.source))

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
                    "resolution": [
                        float(abs(dataset.transform.a)),
                        float(abs(dataset.transform.e)),
                    ],
                    "source": str(path),
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
                data=metadata,
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
