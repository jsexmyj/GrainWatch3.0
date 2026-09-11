# tool_system/tools/bbox.py
from pathlib import Path
from typing import Union

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class BBoxInput(BaseModel):
    """
    边界范围读取工具输入参数模型
    支持直接接收 GeoDataFrame 内存对象或栅格文件路径，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Union[gpd.GeoDataFrame, str, Path] = Field(
        ...,
        description="输入数据源，支持两种形式：geopandas.GeoDataFrame 矢量对象，或栅格文件路径（如 .tif，需可被 rasterio 读取）。",
    )


# ==========================================
# 2. 边界范围读取工具核心类
# ==========================================
class BBoxTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "bbox"

    # 供 Skill/Agent 语义读取的核心描述-按照what-when-output结构输出
    description = (
        "Retrieves the spatial bounding box and extent information of a raster or vector dataset."
        "Use this tool when the spatial coverage, geographic extent, or location of a dataset is required for subsequent analysis or decision making. "
        "Returns the bounding box, coordinate reference system (CRS), extent dimensions, and center point without modifying the input data."
    )

    input_model = BBoxInput
    output_model = ToolResult

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"边界范围读取失败: {result.error or '未知错误'}"

        xmin = result.metadata.get("xmin", "未知")
        ymin = result.metadata.get("ymin", "未知")
        xmax = result.metadata.get("xmax", "未知")
        ymax = result.metadata.get("ymax", "未知")
        return f"已提取空间范围 bbox=({xmin}, {ymin}, {xmax}, {ymax})。"

    async def execute(self, input_data: BBoxInput) -> ToolResult:
        """
        原子级边界范围读取核心逻辑：根据输入类型分别解析矢量或栅格数据的边界
        """
        try:
            source = input_data.source

            if isinstance(source, gpd.GeoDataFrame):
                # 矢量数据：直接使用 total_bounds 获取整体外包框
                xmin, ymin, xmax, ymax = source.total_bounds
                crs = source.crs
            else:
                # 栅格数据：通过 rasterio 读取边界与坐标系
                try:
                    import rasterio
                except ImportError as exc:
                    raise ImportError("读取栅格文件边界需要安装 rasterio") from exc

                path = Path(source)
                if not path.exists():
                    raise FileNotFoundError(f"栅格文件不存在: {path}")

                with rasterio.open(path) as dataset:
                    bounds = dataset.bounds
                    xmin, ymin, xmax, ymax = (
                        bounds.left,
                        bounds.bottom,
                        bounds.right,
                        bounds.top,
                    )
                    crs = dataset.crs

            # 组装边界范围元数据
            metadata = {
                "xmin": float(xmin),
                "ymin": float(ymin),
                "xmax": float(xmax),
                "ymax": float(ymax),
                "crs": str(crs) if crs is not None else None,
                "width": float(xmax - xmin),
                "height": float(ymax - ymin),
                "center": (float((xmin + xmax) / 2), float((ymin + ymax) / 2)),
            }

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="bbox",  # 结果为边界范围描述，非矢量/栅格数据本身
                data=None,
                metadata=metadata,
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="bbox",
                data=None,
                error=f"边界范围读取失败: {str(e)}",
            )
