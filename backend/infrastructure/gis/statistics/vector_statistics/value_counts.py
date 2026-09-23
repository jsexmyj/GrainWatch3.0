from typing import Any, Optional

import geopandas as gpd
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class ValueCountsInput(BaseModel):
    """
    分类频数统计工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(..., description="输入的 GeoDataFrame 矢量数据集。")
    field: str = Field(..., description="用于分类统计的属性字段名。")
    top_n: Optional[int] = Field(
        None,
        ge=1,
        description="可选，仅返回频数最高（或最低）的前 N 个类别。为空则返回全部类别。",
    )
    include_ratio: bool = Field(
        True,
        description="是否返回各类别占比，默认 True。",
    )
    ascending: bool = Field(
        False,
        description="频数排序方式，默认 False（按频数从高到低）。",
    )
    dropna: bool = Field(
        True,
        description="是否在统计时忽略空值（NaN），默认 True。",
    )


# ==========================================
# 2. 分类频数工具核心类
# ==========================================
class ValueCountsTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "value_counts"

    # 供 Skill/Agent 语义读取的核心描述（保持英文）
    description = (
        "Computes categorical value counts for an attribute field in a GeoDataFrame. "
        "Use this tool when class composition, frequency ranking, and optional category proportions are required. "
        "Supports optional top-N truncation for planner-selected concise reporting."
    )

    input_model = ValueCountsInput
    output_model = ToolResult

    @staticmethod
    def _format_number(value: Any) -> str:
        if isinstance(value, (int,)):
            return str(value)
        if isinstance(value, float):
            text = f"{value:.6f}".rstrip("0").rstrip(".")
            return text if text else "0"
        return str(value)

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

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"分类频数统计失败: {result.error or '未知错误'}"

        data = result.data
        if not isinstance(data, pd.DataFrame) or data.empty:
            return "分类频数结果为空。"

        field = result.metadata.get("field", "category")
        include_ratio = result.metadata.get("include_ratio", False)

        facts: list[str] = []
        for _, row in data.iterrows():
            category = self._to_python_scalar(row[field])
            category_text = "空值类别" if category is None else str(category)
            count_text = self._format_number(self._to_python_scalar(row["count"]))

            if include_ratio and "ratio" in data.columns:
                ratio = self._to_python_scalar(row["ratio"])
                ratio_text = f"{float(ratio) * 100:.2f}%"
                facts.append(f"{category_text}数量为{count_text}，占比为{ratio_text}")
            else:
                facts.append(f"{category_text}数量为{count_text}")

        return "，".join(facts) + "。"

    async def execute(self, input_data: ValueCountsInput) -> ToolResult:
        """
        原子级分类频数统计逻辑：直接在内存中基于 GeoDataFrame 执行
        """
        try:
            gdf = input_data.gdf

            field = input_data.field
            if field not in gdf.columns:
                raise KeyError(f"字段 '{field}' 不存在于属性表中")

            value_counts = gdf[field].value_counts(
                dropna=input_data.dropna,
                ascending=input_data.ascending,
            )

            if input_data.top_n is not None:
                value_counts = value_counts.head(input_data.top_n)

            total_count = int(len(gdf))
            denominator_count = (
                int(gdf[field].notna().sum()) if input_data.dropna else total_count
            )
            denominator_scope = "valid_records" if input_data.dropna else "all_records"
            ratio_series = (
                (value_counts / denominator_count)
                if denominator_count > 0
                else value_counts.astype(float)
            )

            rows = []
            for category, count in value_counts.items():
                row = {
                    "category": self._to_python_scalar(category),
                    "count": int(count),
                }
                if input_data.include_ratio:
                    row["ratio"] = float(ratio_series.loc[category])
                rows.append(row)

            freq_df = value_counts.rename_axis(field).reset_index(name="count")
            if input_data.include_ratio:
                freq_df["ratio"] = (
                    (freq_df["count"] / denominator_count)
                    if denominator_count > 0
                    else 0.0
                )

            metadata = {
                "field": field,
                "total_count": total_count,
                "denominator_count": denominator_count,
                "denominator_scope": denominator_scope,
                "category_count": int(gdf[field].nunique(dropna=input_data.dropna)),
                "top_n": input_data.top_n,
                "include_ratio": input_data.include_ratio,
                "ascending": input_data.ascending,
                "dropna": input_data.dropna,
                "rows": rows,
            }

            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="table",
                data=freq_df,
                metadata=metadata,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="table",
                data=None,
                error=f"分类频数统计失败: {str(e)}",
            )
