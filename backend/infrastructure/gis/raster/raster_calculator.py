# tool_system/tools/raster_calculator.py
import os
from pathlib import Path
from typing import Dict, Optional, Union

import numpy as np
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.gis.base_schemas import FileResource
from backend.infrastructure.gis.path_helper import resolve_gis_output_path
from backend.infrastructure.tool_manager.base import BaseTool, ToolResult

# 表达式求值时允许使用的安全函数，避免暴露任意内置方法
_SAFE_FUNCTIONS = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "log": np.log,
    "exp": np.exp,
    "minimum": np.minimum,
    "maximum": np.maximum,
    "where": np.where,
    "np": np,
}


# ==========================================
# 1. 输入模型定义
# ==========================================
class RasterCalculatorInput(BaseModel):
    """
    栅格计算器工具输入参数模型
    支持传入多个栅格文件路径作为参与计算的波段变量
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    rasters: Dict[str, Union[str, Path]] = Field(
        ...,
        description="参与计算的栅格文件映射，键为在表达式中引用的变量名，值为栅格文件路径（如 {'nir': 'nir.tif', 'red': 'red.tif'}）。所有栅格须具有一致的行列数。",
    )
    expression: str = Field(
        ...,
        description="基于 numpy 的计算表达式，使用 rasters 中定义的变量名（如 '(nir - red) / (nir + red + 1e-6)'）。仅支持基础数学运算符及 abs/sqrt/log/exp/minimum/maximum/where 等安全函数。",
    )
    band: int = Field(
        1,
        description="所有输入栅格参与计算的波段序号（从 1 开始），默认取第 1 波段。",
    )
    dtype: str = Field(
        "float32",
        description="输出栅格的像元数据类型，默认 'float32'。",
    )
    nodata: Optional[float] = Field(
        None,
        description="可选，输出栅格的 nodata 值。为空则不设置。",
    )
    destination: Optional[str] = Field(
        None,
        description="输出 tif 文件路径。为空时将在默认结果目录下自动生成文件名。",
    )
    file_name: Optional[str] = Field(
        None,
        description="当 destination 为空时使用的文件名（不含后缀），默认自动生成。",
    )


# ==========================================
# 2. 栅格计算器工具核心类
# ==========================================
class RasterCalculatorTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "raster_calculator"

    # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
    description = (
        "Performs pixel-wise math on one or more input rasters using a "
        "user-supplied numpy expression (e.g. band ratios, index "
        "calculations such as NDVI, or thresholding). Use this tool when a "
        "new raster needs to be derived from existing raster bands through "
        "an arithmetic or logical expression. Returns a FileResource "
        "pointing to the newly generated GeoTIFF raster."
    )

    input_model = RasterCalculatorInput
    output_model = ToolResult

    async def execute(self, input_data: RasterCalculatorInput) -> ToolResult:
        """
        原子级栅格计算核心逻辑：读取多幅栅格波段并按表达式逐像元计算，写出新的 GeoTIFF
        """
        try:
            import rasterio

            if not input_data.rasters:
                raise ValueError("rasters 不能为空，至少需要一个输入栅格")

            arrays = {}
            reference_meta = None

            # 逐个读取参与计算的栅格波段，并校验尺寸一致性
            for alias, raster_path in input_data.rasters.items():
                path = Path(raster_path)
                if not path.exists():
                    raise FileNotFoundError(f"栅格文件不存在: {path}")

                with rasterio.open(path) as dataset:
                    array = dataset.read(input_data.band).astype("float64")
                    if reference_meta is None:
                        reference_meta = dataset.meta.copy()
                    else:
                        # 校验行列数、坐标系、仿射变换三者均须与基准栅格一致，
                        # 否则逐像元运算会错位，必须先重采样/重投影对齐
                        if array.shape != (
                            reference_meta["height"],
                            reference_meta["width"],
                        ):
                            raise ValueError(
                                f"栅格 '{alias}' 的行列数与基准栅格不一致，请先重采样对齐"
                            )
                        if dataset.crs != reference_meta["crs"]:
                            raise ValueError(
                                f"栅格 '{alias}' 的坐标系（{dataset.crs}）与基准栅格"
                                f"（{reference_meta['crs']}）不一致，请先使用 raster_reproject "
                                "工具统一坐标系后再计算"
                            )
                        if dataset.transform != reference_meta["transform"]:
                            raise ValueError(
                                f"栅格 '{alias}' 的仿射变换（分辨率/原点）与基准栅格不一致，"
                                "请先重采样/重投影对齐后再计算"
                            )
                    arrays[alias] = array

            # 组装安全的表达式求值命名空间：仅包含输入栅格变量与白名单函数
            eval_namespace = {**_SAFE_FUNCTIONS, **arrays}
            result_array = eval(
                input_data.expression, {"__builtins__": {}}, eval_namespace
            )
            result_array = np.asarray(result_array).astype(input_data.dtype)

            destination = resolve_gis_output_path(
                destination=input_data.destination,
                file_name=input_data.file_name,
                tool_suffix="raster_calculator",
            )

            out_meta = reference_meta.copy()
            out_meta.update(
                {"count": 1, "dtype": input_data.dtype, "nodata": input_data.nodata}
            )

            with rasterio.open(destination, "w", **out_meta) as dst:
                dst.write(result_array, 1)

            file_resource = FileResource(
                path=os.path.abspath(destination),
                file_name=os.path.basename(destination),
                format="tif",
                size_bytes=os.path.getsize(destination),
                metadata={
                    "width": out_meta["width"],
                    "height": out_meta["height"],
                    "crs": str(out_meta.get("crs")) if out_meta.get("crs") else None,
                },
            )

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="file",
                data=file_resource,
                metadata={
                    "expression": input_data.expression,
                    "inputs": list(input_data.rasters.keys()),
                    "width": out_meta["width"],
                    "height": out_meta["height"],
                },
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="file",
                data=None,
                error=f"栅格计算失败: {str(e)}",
            )
