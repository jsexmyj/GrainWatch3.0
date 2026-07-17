# tool_system/tools/summary.py
from typing import List, Optional

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class FieldSummaryInput(BaseModel):
    """
    汇总统计工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(..., description="输入的 GeoDataFrame 矢量数据集。")
    fields: Optional[List[str]] = Field(
        None,
        description="可选，参与汇总统计的数值型字段列表。为空则自动选取所有数值型属性字段。",
    )
    group_by: Optional[str] = Field(
        None,
        description="可选，按该属性字段分组后再分别汇总统计（如按地物类型统计各类面积均值）。为空则对全表统计。",
    )
    stats: List[str] = Field(
        default_factory=lambda: ["count", "sum", "mean", "min", "max", "std"],
        description=(
            "需要计算的统计量列表，支持 pandas 聚合函数名，"
            "如 count/sum/mean/median/min/max/std/var 等，默认计算常用六项。"
        ),
    )


# ==========================================
# 2. 汇总统计工具核心类
# ==========================================
class FieldStatisticsTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "field_summary"

    # 供 Skill/Agent 语义读取的核心描述（保持英文）
    description = (
        "Computes descriptive statistics for one or more numeric attribute fields."
        "Use this tool to summarize numeric attributes such as area,population or elevation."
        "Returns descriptive statistics including count, mean, min,max, sum and standard deviation."
    )

    input_model = FieldSummaryInput
    output_model = ToolResult

    async def execute(self, input_data: FieldSummaryInput) -> ToolResult:
        """
        原子级汇总统计核心逻辑：直接在内存中基于 GeoDataFrame 执行
        """
        try:
            gdf = input_data.gdf

            # 确定参与统计的字段：显式指定，或自动选取所有数值型字段
            if input_data.fields:
                fields = input_data.fields
                for f in fields:
                    if f not in gdf.columns:
                        raise KeyError(f"字段 '{f}' 不存在于属性表中")
            else:
                fields = gdf.select_dtypes(include="number").columns.tolist()

            if not fields:
                raise ValueError(
                    "未找到可用于汇总统计的数值型字段，请通过 fields 参数显式指定"
                )

            metadata = {
                "fields": fields,
                "stats": input_data.stats,
            }

            # 分组汇总 或 全表汇总
            if input_data.group_by:
                if input_data.group_by not in gdf.columns:
                    raise KeyError(f"分组字段 '{input_data.group_by}' 不存在于属性表中")
                summary_result = gdf.groupby(input_data.group_by)[fields].agg(
                    input_data.stats
                )
                # 展平多级列索引，便于消费
                summary_result.columns = [
                    "_".join(col).strip("_")
                    for col in summary_result.columns.to_flat_index()
                ]
                summary_result = summary_result.reset_index()
                metadata["group_by"] = input_data.group_by
            else:
                summary_result = gdf[fields].agg(input_data.stats)

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="table",  # 结果为统计表，非矢量几何
                data=summary_result,
                metadata=metadata,
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="table",
                data=None,
                error=f"汇总统计失败: {str(e)}",
            )
