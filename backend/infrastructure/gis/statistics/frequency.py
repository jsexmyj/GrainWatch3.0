# tool_system/tools/frequency.py
from typing import Optional

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class FrequencyInput(BaseModel):
    """
    频数统计工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(..., description="输入的 GeoDataFrame 矢量数据集。")
    field: str = Field(
        ..., description="用于计算频数分布的属性字段名（如地物类型字段）。"
    )
    normalize: bool = Field(
        False,
        description="是否返回各取值所占比例（相对频率），默认为 False，返回绝对频数。",
    )
    ascending: bool = Field(
        False, description="频数结果排序方式，默认为 False（按频数从高到低排列）。"
    )
    dropna: bool = Field(True, description="是否在统计时忽略空值（NaN），默认为 True。")
    top_n: Optional[int] = Field(
        None,
        description="可选，仅返回频数最高（或最低，取决于 ascending）的前 N 个取值。为空则返回全部。",
    )


# ==========================================
# 2. 频数统计工具核心类
# ==========================================
class FrequencyTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "frequency"

    # 供 Skill/Agent 语义读取的核心描述（保持英文）
    description = (
        "Calculates the frequency distribution of categorical attribute values."
        "Use this tool to summarize class composition or category proportions."
        "Returns a frequency table."
    )

    input_model = FrequencyInput
    output_model = ToolResult

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"频数统计失败: {result.error or '未知错误'}"

        field = result.metadata.get("field", "未知字段")
        unique_values = result.metadata.get("unique_values", "未知")
        return f"已完成字段 {field} 的频数统计，共识别{unique_values}个类别。"

    async def execute(self, input_data: FrequencyInput) -> ToolResult:
        """
        原子级频数统计核心逻辑：直接在内存中基于 GeoDataFrame 执行
        """
        try:
            gdf = input_data.gdf
            field = input_data.field

            if field not in gdf.columns:
                raise KeyError(f"字段 '{field}' 不存在于属性表中")

            # 计算频数分布（value_counts 本身按频数降序，再根据 ascending 参数调整）
            freq_series = gdf[field].value_counts(
                normalize=input_data.normalize,
                dropna=input_data.dropna,
                ascending=input_data.ascending,
            )

            # 截取前 N 项
            if input_data.top_n is not None:
                freq_series = freq_series.head(input_data.top_n)

            # 转换为 DataFrame，便于后续工具链或前端展示消费
            freq_df = freq_series.rename_axis(field).reset_index(
                name=("proportion" if input_data.normalize else "count")
            )

            metadata = {
                "field": field,
                "unique_values": int(gdf[field].nunique(dropna=input_data.dropna)),
                "total_count": int(len(gdf)),
                "normalize": input_data.normalize,
            }

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="table",  # 结果为统计表，非矢量几何
                data=freq_df,
                metadata=metadata,
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="table",
                data=None,
                error=f"频数统计失败: {str(e)}",
            )
