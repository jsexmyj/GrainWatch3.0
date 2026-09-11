# tool_system/tools/length.py
from typing import Optional

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class LengthInput(BaseModel):
    """
    长度计算工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(
        ...,
        description="输入的 GeoDataFrame 矢量数据集。调用前须确保其已处于正确的投影坐标系中（长度单位取决于该坐标系单位）。对于面要素将计算其外边界周长。",
    )
    length_field_name: str = Field(
        "length",
        description="计算结果写回 GeoDataFrame 时使用的新字段名，默认写为 'length'。",
    )
    decimals: Optional[int] = Field(
        6, description="可选，长度结果保留的小数位数。为空则不进行四舍五入。"
    )


# ==========================================
# 2. 长度计算工具核心类
# ==========================================
class LengthTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "length"

    # 供 Skill/Agent 语义读取的核心描述（保持英文）
    description = (
        "Measures the geometric length (or polygon perimeter) of each feature in a GeoDataFrame. "
        "Use this tool before distance- or length-based statistical analysis."
        "Returns the input GeoDataFrame with an additional length field."
    )

    input_model = LengthInput
    output_model = ToolResult

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"长度计算失败: {result.error or '未知错误'}"

        field_name = result.metadata.get("length_field_name", "length")
        feature_count = result.metadata.get("feature_count", "未知")
        return f"已为{feature_count}个要素计算长度并写入字段 {field_name}。"

    async def execute(self, input_data: LengthInput) -> ToolResult:
        """
        原子级长度计算核心逻辑：直接在内存中基于 GeoDataFrame 执行
        """
        try:
            gdf = input_data.gdf
            field_name = input_data.length_field_name

            # 逐要素计算长度：
            # - 线要素：geometry.length 即为线段长度
            # - 面要素：geometry.length 等价于其外边界周长
            lengths = gdf.geometry.length
            if input_data.decimals is not None:
                lengths = lengths.round(input_data.decimals)

            # 写回新的 GeoDataFrame，保留原属性表
            result_gdf = gdf.copy()
            result_gdf[field_name] = lengths

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="vector",  # 固定为矢量类型
                data=result_gdf,  # 返回带长度字段的 GeoDataFrame
                metadata={
                    "length_field_name": field_name,
                    "feature_count": int(len(result_gdf)),
                },
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="vector",
                data=None,
                error=f"长度计算失败: {str(e)}",
            )
