from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult
from backend.utils.paths import resolve_file_path


class RasterValueCountsInput(BaseModel):
    """
    栅格像元值/类别计数工具输入参数模型
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Union[str, Path] = Field(
        ...,
        description="输入栅格文件路径，支持相对路径（相对项目根目录）和绝对路径。",
    )
    band: int = Field(1, ge=1, description="统计的波段编号（从 1 开始）。")
    include_nodata: bool = Field(False, description="是否将 NoData 像元纳入统计。")
    top_n: Optional[int] = Field(
        None,
        ge=1,
        description="可选，仅返回像元数量最多的前 N 个类别。为空则返回全部类别。",
    )


class RasterValueCountsTool(BaseTool):
    name = "raster_value_counts"
    description = (
        "Counts raster pixel values/classes for a specified band. "
        "Use this tool when class composition and pixel-count distribution are required. "
        "Returns a value-count table with optional ratio for each class."
    )

    input_model = RasterValueCountsInput
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
            return f"栅格像元计数失败: {result.error or '未知错误'}"

        data = result.data
        if not isinstance(data, pd.DataFrame) or data.empty:
            return "栅格像元计数结果为空。"

        facts: list[str] = []
        for _, row in data.head(5).iterrows():
            value_text = self._format_number(self._to_python_scalar(row["value"]))
            count_text = self._format_number(self._to_python_scalar(row["pixel_count"]))
            ratio_text = f"{float(row['ratio']) * 100:.2f}%"
            facts.append(f"像元值{value_text}数量为{count_text}，占比为{ratio_text}")

        if len(data) > 5:
            facts.append(f"其余{len(data) - 5}个类别已省略")

        return "，".join(facts) + "。"

    async def execute(self, input_data: RasterValueCountsInput) -> ToolResult:
        try:
            import rasterio

            source_path = resolve_file_path(str(input_data.source))

            with rasterio.open(source_path) as dataset:
                if input_data.band > dataset.count:
                    raise ValueError(
                        f"波段 {input_data.band} 超出范围，当前栅格仅有 {dataset.count} 个波段"
                    )

                nodata_value = dataset.nodata
                if input_data.include_nodata:
                    raw_values = dataset.read(input_data.band, masked=False).ravel()
                    valid_values = raw_values
                else:
                    masked = dataset.read(input_data.band, masked=True)
                    valid_values = masked.compressed()

            if valid_values.size == 0:
                return ToolResult(
                    success=True,
                    tool_name=self.name,
                    result_type="table",
                    data=pd.DataFrame(columns=["value", "pixel_count", "ratio"]),
                    metadata={
                        "source": str(source_path),
                        "band": input_data.band,
                        "include_nodata": input_data.include_nodata,
                        "nodata": nodata_value,
                        "total_pixels": 0,
                        "unique_class_count": 0,
                        "rows": [],
                    },
                )

            values, counts = np.unique(valid_values, return_counts=True)
            count_df = pd.DataFrame(
                {
                    "value": values,
                    "pixel_count": counts.astype(int),
                }
            ).sort_values(by="pixel_count", ascending=False)

            total_pixels = int(count_df["pixel_count"].sum())
            count_df["ratio"] = count_df["pixel_count"] / total_pixels

            if input_data.top_n is not None:
                count_df = count_df.head(input_data.top_n).copy()

            rows = [
                {
                    "value": self._to_python_scalar(row["value"]),
                    "pixel_count": int(row["pixel_count"]),
                    "ratio": float(row["ratio"]),
                }
                for _, row in count_df.iterrows()
            ]

            metadata = {
                "source": str(source_path),
                "band": input_data.band,
                "include_nodata": input_data.include_nodata,
                "nodata": self._to_python_scalar(nodata_value),
                "total_pixels": total_pixels,
                "unique_class_count": int(len(count_df)),
                "rows": rows,
            }

            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="table",
                data=count_df.reset_index(drop=True),
                metadata=metadata,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="table",
                data=None,
                error=f"栅格像元计数失败: {str(e)}",
            )
