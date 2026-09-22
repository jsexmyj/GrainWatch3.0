from pathlib import Path
from typing import Any, Literal, Optional, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult
from backend.utils.paths import resolve_file_path


class RasterClassAreaInput(BaseModel):
    """
    栅格分类面积统计工具输入参数模型
    基于像元数量和分辨率计算各类别面积
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Union[str, Path] = Field(
        ...,
        description="输入栅格文件路径，支持相对路径（相对项目根目录）和绝对路径。",
    )
    band: int = Field(1, ge=1, description="统计的波段编号（从 1 开始）。")
    include_nodata: bool = Field(False, description="是否将 NoData 像元纳入面积统计。")
    area_unit: Literal["map_unit2", "hectare", "square_kilometer"] = Field(
        "map_unit2",
        description="面积单位：map_unit2（坐标单位平方）、hectare（公顷）、square_kilometer（平方千米）。",
    )
    top_n: Optional[int] = Field(
        None,
        ge=1,
        description="可选，仅返回面积最大的前 N 个类别。为空则返回全部类别。",
    )


class RasterClassAreaTool(BaseTool):
    name = "raster_class_area"
    description = (
        "Computes raster class areas by combining per-class pixel counts with pixel resolution. "
        "Use this tool when area distribution by class is needed for raster classification analysis. "
        "Returns class-wise area table and summary metadata."
    )

    input_model = RasterClassAreaInput
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

    @staticmethod
    def _unit_label(area_unit: str) -> str:
        if area_unit == "hectare":
            return "公顷"
        if area_unit == "square_kilometer":
            return "平方千米"
        return "坐标单位平方"

    @staticmethod
    def _unit_factor(area_unit: str) -> float:
        if area_unit == "hectare":
            return 1.0 / 10000.0
        if area_unit == "square_kilometer":
            return 1.0 / 1000000.0
        return 1.0

    def build_fact(self, result: ToolResult) -> str:
        if not result.success:
            return f"栅格分类面积统计失败: {result.error or '未知错误'}"

        data = result.data
        if not isinstance(data, pd.DataFrame) or data.empty:
            return "栅格分类面积统计结果为空。"

        area_unit = str(result.metadata.get("area_unit", "map_unit2"))
        unit_label = self._unit_label(area_unit)

        facts: list[str] = []
        for _, row in data.head(5).iterrows():
            value_text = self._format_number(self._to_python_scalar(row["value"]))
            area_text = self._format_number(self._to_python_scalar(row["area"]))
            facts.append(f"像元值{value_text}面积为{area_text}{unit_label}")

        if len(data) > 5:
            facts.append(f"其余{len(data) - 5}个类别已省略")

        return "，".join(facts) + "。"

    async def execute(self, input_data: RasterClassAreaInput) -> ToolResult:
        try:
            import rasterio

            source_path = resolve_file_path(str(input_data.source))

            with rasterio.open(source_path) as dataset:
                if input_data.band > dataset.count:
                    raise ValueError(
                        f"波段 {input_data.band} 超出范围，当前栅格仅有 {dataset.count} 个波段"
                    )

                nodata_value = dataset.nodata
                res_x = float(abs(dataset.transform.a))
                res_y = float(abs(dataset.transform.e))
                pixel_area_native = res_x * res_y

                if input_data.include_nodata:
                    raw_values = dataset.read(input_data.band, masked=False).ravel()
                    valid_values = raw_values
                else:
                    masked = dataset.read(input_data.band, masked=True)
                    valid_values = masked.compressed()

            if valid_values.size == 0:
                empty_df = pd.DataFrame(columns=["value", "pixel_count", "area"])
                return ToolResult(
                    success=True,
                    tool_name=self.name,
                    result_type="table",
                    data=empty_df,
                    metadata={
                        "source": str(source_path),
                        "band": input_data.band,
                        "include_nodata": input_data.include_nodata,
                        "nodata": nodata_value,
                        "resolution": [res_x, res_y],
                        "pixel_area_native": pixel_area_native,
                        "area_unit": input_data.area_unit,
                        "total_area": 0.0,
                        "rows": [],
                    },
                )

            values, counts = np.unique(valid_values, return_counts=True)
            unit_factor = self._unit_factor(input_data.area_unit)

            area_df = pd.DataFrame(
                {
                    "value": values,
                    "pixel_count": counts.astype(int),
                }
            )
            area_df["area"] = area_df["pixel_count"] * pixel_area_native * unit_factor
            area_df = area_df.sort_values(by="area", ascending=False)

            if input_data.top_n is not None:
                area_df = area_df.head(input_data.top_n).copy()

            rows = [
                {
                    "value": self._to_python_scalar(row["value"]),
                    "pixel_count": int(row["pixel_count"]),
                    "area": float(row["area"]),
                }
                for _, row in area_df.iterrows()
            ]

            metadata = {
                "source": str(source_path),
                "band": input_data.band,
                "include_nodata": input_data.include_nodata,
                "nodata": self._to_python_scalar(nodata_value),
                "resolution": [res_x, res_y],
                "pixel_area_native": pixel_area_native,
                "area_unit": input_data.area_unit,
                "total_area": float(area_df["area"].sum()),
                "rows": rows,
            }

            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="table",
                data=area_df.reset_index(drop=True),
                metadata=metadata,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="table",
                data=None,
                error=f"栅格分类面积统计失败: {str(e)}",
            )
