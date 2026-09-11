# tool_system/tools/count.py
from typing import Optional

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class CountInput(BaseModel):
    """
    数量统计工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(
        ..., description="输入的 GeoDataFrame 矢量数据集，用于统计要素数量。"
    )
    group_by: Optional[str] = Field(
        None,
        description="可选，按该属性字段分组统计数量（如按地物类型统计地块个数）。为空则只统计总数。",
    )
    query: Optional[str] = Field(
        None,
        description='可选，符合 pandas.DataFrame.query 语法的过滤表达式，先筛选再统计（如 "area > 100"）。',
    )


# ==========================================
# 2. 数量统计工具核心类
# ==========================================
class CountTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "count"

    # 供 Skill/Agent 语义读取的核心描述（保持英文）
    description = (
        "Counts the number of features in a GeoDataFrame."
        "Use this tool whenever the total number of spatial objects is required."
        "Returns the total feature count."
    )

    input_model = CountInput
    output_model = ToolResult

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"数量统计失败: {result.error or '未知错误'}"

        total_count = result.metadata.get("total_count", "未知")
        group_by = result.metadata.get("group_by")
        if group_by:
            return f"已完成数量统计，总要素数为{total_count}，分组字段为 {group_by}。"
        return f"已完成数量统计，总要素数为{total_count}。"

    async def execute(self, input_data: CountInput) -> ToolResult:
        """
        原子级数量统计核心逻辑：直接在内存中基于 GeoDataFrame 执行
        """
        try:
            gdf = input_data.gdf

            # 若指定了过滤条件，先执行筛选
            if input_data.query:
                gdf = gdf.query(input_data.query)

            total_count = int(len(gdf))
            metadata = {"total_count": total_count}

            # 若指定分组字段，则按组统计数量
            if input_data.group_by:
                if input_data.group_by not in gdf.columns:
                    raise KeyError(f"分组字段 '{input_data.group_by}' 不存在于属性表中")
                grouped = gdf.groupby(input_data.group_by).size()
                metadata["group_by"] = input_data.group_by
                metadata["count_by_group"] = grouped.to_dict()

            # 组装统一的成功返回格式
            # 数据本身不做几何变换，返回筛选后的 GeoDataFrame 便于后续工具链复用
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="vector",
                data=gdf,
                metadata=metadata,
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="vector",
                data=None,
                error=f"数量统计失败: {str(e)}",
            )
