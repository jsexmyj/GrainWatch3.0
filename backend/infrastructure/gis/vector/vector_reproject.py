# tool_system/tools/vector_reproject.py
from typing import Literal, Optional, Union

import geopandas as gpd
from pydantic import BaseModel, Field, ConfigDict

from backend.infrastructure.gis.coordinate.detect_crs import (
    ZoneWidth,
    build_gauss_kruger_crs,
    compute_gauss_kruger_zone,
)
from backend.infrastructure.tool_manager.base import BaseTool, ToolResult


# ==========================================
# 1. 输入模型定义
# ==========================================
class VectorReprojectInput(BaseModel):
    """
    矢量投影工具输入参数模型
    直接接收 GeoDataFrame 内存对象，需开启任意类型支持
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gdf: gpd.GeoDataFrame = Field(
        ...,
        description="输入的 GeoDataFrame 矢量数据集，须具备已知的坐标系（gdf.crs 不能为空）。",
    )
    mode: Literal["manual", "auto"] = Field(
        "manual",
        description="投影模式：'manual' 为指定转换，须提供 target_crs；'auto' 为自动转换，"
        "根据数据的地理位置自动计算最合适的高斯克吕格投影坐标系。",
    )
    target_crs: Optional[Union[str, int]] = Field(
        None,
        description="mode='manual' 时必填，目标坐标系（EPSG 码或 PROJ/WKT 字符串）。",
    )
    zone_width: ZoneWidth = Field(
        3,
        description="mode='auto' 时使用，高斯克吕格分带宽度，支持 3 度带或 6 度带，默认 3 度带。",
    )
    ellipsoid: str = Field(
        "CGCS2000",
        description="mode='auto' 时使用，参考椭球体名称（pyproj 可识别），默认 'CGCS2000'。",
    )


# ==========================================
# 2. 矢量投影工具核心类
# ==========================================
class VectorReprojectTool(BaseTool):
    # 供 Skill 调用的唯一标识
    name = "vector_reproject"

    # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
    description = (
        "Reprojects a vector GeoDataFrame to a projected coordinate "
        "system, either a user-specified CRS (manual mode) or an "
        "automatically determined Gauss-Kruger projection based on the "
        "data's geographic location (auto mode). Use this tool when "
        "vector data needs to be converted into a metric projected CRS "
        "before area/length/distance calculations, or aligned with "
        "another dataset's CRS. Returns the reprojected GeoDataFrame."
    )

    input_model = VectorReprojectInput
    output_model = ToolResult

    async def execute(self, input_data: VectorReprojectInput) -> ToolResult:
        """
        原子级矢量投影核心逻辑：manual 模式直接 to_crs，auto 模式先探测坐标系再转换
        """
        try:
            gdf = input_data.gdf
            if gdf.crs is None:
                raise ValueError(
                    "输入的 GeoDataFrame 缺少坐标系（crs），无法进行投影转换"
                )

            metadata = {"mode": input_data.mode, "source_crs": str(gdf.crs)}

            if input_data.mode == "manual":
                if not input_data.target_crs:
                    raise ValueError("mode='manual' 时必须提供 target_crs")
                target_crs = input_data.target_crs
                result_gdf = gdf.to_crs(target_crs)
                metadata["target_crs"] = str(result_gdf.crs)

            else:  # auto 模式：先获取地理坐标下的中心点经纬度，再探测适配的高斯克吕格投影
                cgcs_gdf = gdf if gdf.crs.to_epsg() == 4490 else gdf.to_crs(4490)
                xmin, ymin, xmax, ymax = cgcs_gdf.total_bounds
                longitude, latitude = (xmin + xmax) / 2, (ymin + ymax) / 2

                zone, central_meridian = compute_gauss_kruger_zone(
                    longitude, input_data.zone_width
                )
                target_crs = build_gauss_kruger_crs(
                    longitude=longitude,
                    latitude=latitude,
                    zone_width=input_data.zone_width,
                    ellipsoid=input_data.ellipsoid,
                )
                result_gdf = gdf.to_crs(target_crs)

                metadata.update(
                    {
                        "target_crs": target_crs.to_proj4(),
                        "zone": zone,
                        "zone_width": input_data.zone_width,
                        "central_meridian": central_meridian,
                        "centroid_lonlat": [float(longitude), float(latitude)],
                    }
                )

            # 组装统一的成功返回格式
            return ToolResult(
                success=True,
                tool_name=self.name,
                result_type="vector",
                data=result_gdf,
                metadata=metadata,
            )

        except Exception as e:
            # 捕获异常并统一返回格式
            return ToolResult(
                success=False,
                tool_name=self.name,
                result_type="vector",
                data=None,
                error=f"矢量投影失败: {str(e)}",
            )
