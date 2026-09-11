# tool_system/tools/buffer.py
import geopandas as gpd
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class BufferInput(BaseModel):
    """
    缓冲工具输入参数模型
    由于直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    gdf: gpd.GeoDataFrame = Field(
        ...,
        description="输入的 GeoDataFrame 矢量数据集。调用前须确保其已处于正确的投影坐标系中。",
    )
    distance: float = Field(
        ...,
        description="缓冲距离数值，将按 unit 转换为实际计算距离。",
    )
    unit: Literal["米", "千米"] = Field("米", description="缓冲距离单位。")
    resolution: int = Field(
        16,
        description="缓冲圆弧的近似分辨率。值越大，缓冲区边界越平滑（对应 GeoPandas 的 resolution 参数）。",
    )


_DISTANCE_UNIT_SCALE = {
    "米": 1.0,
    "千米": 1000.0,
}


# ==========================================
# 2. 缓冲工具核心类
# ==========================================
class BufferTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "buffer"

    # 供 Skill/Agent 语义读取的核心描述（保持英文）
    description = (
        "Generates a buffer zone around spatial features. "
        "It accepts a geopandas.GeoDataFrame as input and returns "
        "the buffered geopandas.GeoDataFrame. "
        "This tool is projection-agnostic and expects the input data "
        "to be already in the desired projected coordinate system."
    )

    input_model = BufferInput
    output_model = ToolResult

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"缓冲区构建失败: {result.error or '未知错误'}"

        distance = result.metadata.get("distance")
        unit = result.metadata.get("unit", "")
        if distance is None:
            return "已对目标空间对象构建缓冲区。"
        return f"已对目标空间对象构建{distance}{unit}缓冲区。"

    async def execute(self, input_data: BufferInput) -> ToolResult:
        """
        原子级缓冲核心逻辑：直接在内存中基于 GeoDataFrame 执行
        """
        try:
            gdf = input_data.gdf
            distance_applied = (
                input_data.distance * _DISTANCE_UNIT_SCALE[input_data.unit]
            )

            # 执行纯粹的缓冲计算
            buffered_geometry = gdf.geometry.buffer(
                distance=distance_applied,
                resolution=input_data.resolution,
            )

            # 生成新的 GeoDataFrame 并保留原属性表
            buffered_gdf = gdf.copy()
            buffered_gdf.geometry = buffered_geometry

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="vector",  # 固定为矢量类型
                data=buffered_gdf,  # 直接返回内存中的 GeoDataFrame
                metadata={
                    "distance": input_data.distance,
                    "unit": input_data.unit,
                    "distance_applied": input_data.distance,
                    "resolution": input_data.resolution,
                },
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="vector",
                data=None,
                error=f"缓冲计算失败: {str(e)}",
            )
