# tool_system/tools/topN.py
from typing import Literal

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class TopNInput(BaseModel):
    """
    TopN 提取工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(
        ...,
        description="输入的 GeoDataFrame 矢量数据集。",
    )
    n: int = Field(
        5,
        description="需要提取的前 N 项记录数量，默认取前 10 项。",
    )
    field: str = Field(
        ...,
        description="用于排序的属性字段名。",
    )
    order: Literal["asc", "desc"] = Field(
        "desc",
        description="排序方式：'asc' 为升序（取最小的 N 项），'desc' 为降序（取最大的 N 项），默认降序。",
    )


# ==========================================
# 2. TopN 提取工具核心类
# ==========================================
class TopNTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "top_n"

    # 供 Skill/Agent 语义读取的核心描述（保持英文）
    description = (
        "Selects the top N features from a GeoDataFrame based on the values of a specified attribute field. "
        "Use this tool when identifying the largest, smallest, highest-ranked, or lowest-ranked spatial features "
        "is required before further analysis or reporting. "
        "Returns a new GeoDataFrame containing the selected features while preserving their original geometries and attributes."
    )

    input_model = TopNInput
    output_model = ToolResult

    async def execute(self, input_data: TopNInput) -> ToolResult:
        """
        原子级 TopN 提取核心逻辑：直接在内存中基于 GeoDataFrame 执行
        """
        try:
            gdf = input_data.gdf
            field = input_data.field

            if field not in gdf.columns:
                raise KeyError(f"字段 '{field}' 不存在于属性表中")

            # 根据排序方式对属性表排序，并截取前 N 项
            ascending = input_data.order == "asc"
            top_gdf = gdf.sort_values(by=field, ascending=ascending).head(input_data.n)

            # 提取前 N 项对应的字段取值，便于结果直接消费
            top_values = top_gdf[field].tolist()

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="vector",  # 固定为矢量类型
                data=top_gdf,  # 返回排序截取后的 GeoDataFrame
                metadata={
                    "field": field,
                    "order": input_data.order,
                    "n": input_data.n,
                    "top_values": top_values,
                },
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="vector",
                data=None,
                error=f"TopN 提取失败: {str(e)}",
            )
