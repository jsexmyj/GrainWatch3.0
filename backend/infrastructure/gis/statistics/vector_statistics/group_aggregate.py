from typing import Any, Literal, Optional

import geopandas as gpd
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class GroupAggregateInput(BaseModel):
    """
    分组聚合统计工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(..., description="输入的 GeoDataFrame 矢量数据集。")
    group_by: str = Field(
        ..., description="分组字段名（如 non_grain_type）。说明按谁分类"
    )
    metric: Optional[str] = Field(
        None,
        description="聚合指标字段名。aggregate=count 时可为空；其他聚合方式必须指定。说明统计谁，比如统计面积",
    )
    aggregate: Literal["count", "sum", "mean", "min", "max"] = Field(
        ...,
        description="聚合函数，支持 count/sum/mean/min/max。",
    )
    ascending: bool = Field(
        False,
        description="聚合结果排序方式，默认 False（从大到小）。",
    )


# ==========================================
# 2. 分组聚合工具核心类
# ==========================================
class GroupAggregateTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "group_aggregate"

    # 供 Skill/Agent 语义读取的核心描述（保持英文）
    description = (
        "Computes grouped aggregation on a GeoDataFrame by category field. "
        "Use this tool to calculate count, sum, mean, min, or max metrics for each group. "
        "Returns a structured aggregation table suitable for quantitative GIS analysis and reporting."
    )

    input_model = GroupAggregateInput
    output_model = ToolResult

    @staticmethod
    def _to_python_scalar(value: Any) -> Any:
        if pd.isna(value):
            return None
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return value
        return value

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
            return f"分组聚合失败: {result.error or '未知错误'}"

        data = result.data
        if not isinstance(data, pd.DataFrame) or data.empty:
            return "分组聚合结果为空。"

        metric = result.metadata.get("metric")
        aggregate = str(result.metadata.get("aggregate", "count"))
        aggregate_text_map = {
            "count": "数量",
            "sum": "总和",
            "mean": "平均值",
            "min": "最小值",
            "max": "最大值",
        }
        aggregate_text = aggregate_text_map.get(aggregate, aggregate)

        facts: list[str] = []
        for _, row in data.iterrows():
            group_value = self._to_python_scalar(row.iloc[0])
            group_text = "空值类别" if group_value is None else str(group_value)
            value = self._to_python_scalar(row["value"])
            value_text = self._format_number(value)

            if metric:
                facts.append(f"{group_text}{metric}{aggregate_text}为{value_text}")
            else:
                facts.append(f"{group_text}{aggregate_text}为{value_text}")

        return "，".join(facts) + "。"

    async def execute(self, input_data: GroupAggregateInput) -> ToolResult:
        """
        原子级分组聚合统计逻辑：直接在内存中基于 GeoDataFrame 执行
        """
        try:
            gdf = input_data.gdf

            if input_data.group_by not in gdf.columns:
                raise KeyError(f"分组字段 '{input_data.group_by}' 不存在于属性表中")

            aggregate = input_data.aggregate
            metric = input_data.metric

            if aggregate == "count":
                if metric:
                    if metric not in gdf.columns:
                        raise KeyError(f"聚合字段 '{metric}' 不存在于属性表中")
                    agg_series = gdf.groupby(input_data.group_by)[metric].count()
                else:
                    agg_series = gdf.groupby(input_data.group_by).size()
            else:
                if not metric:
                    raise ValueError(f"aggregate='{aggregate}' 时必须提供 metric 字段")
                if metric not in gdf.columns:
                    raise KeyError(f"聚合字段 '{metric}' 不存在于属性表中")
                if not pd.api.types.is_numeric_dtype(gdf[metric]):
                    raise TypeError(
                        f"聚合字段 '{metric}' 必须为数值类型，当前类型为 {gdf[metric].dtype}"
                    )
                agg_series = gdf.groupby(input_data.group_by)[metric].agg(aggregate)

            agg_series = agg_series.sort_values(ascending=input_data.ascending)
            result_df = agg_series.rename("value").reset_index()

            rows = [
                {
                    "group": self._to_python_scalar(row[input_data.group_by]),
                    "value": self._to_python_scalar(row["value"]),
                }
                for _, row in result_df.iterrows()
            ]

            metadata = {
                "group_by": input_data.group_by,
                "metric": metric,
                "aggregate": aggregate,
                "ascending": input_data.ascending,
                "total_count": int(len(gdf)),
                "group_count": int(len(result_df)),
                "rows": rows,
            }

            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="table",
                data=result_df,
                metadata=metadata,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="table",
                data=None,
                error=f"分组聚合失败: {str(e)}",
            )
