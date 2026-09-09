# tool_system/tools/detect_crs.py
"""
坐标系探测核心逻辑模块。

仅包含"根据经纬度推算高斯克吕格投影坐标系"的纯函数实现，
不依赖 GeoDataFrame/BaseTool，供 vector_reproject 等上层工具复用，
以保持模块间的低耦合。
"""

from typing import Literal, Tuple

from pydantic import BaseModel, Field, ConfigDict
from pyproj import CRS

from backend.infrastructure.tool_manager.base import BaseTool, ToolResult
from backend.utils.logger import get_logger

ZoneWidth = Literal[3, 6]
logger = get_logger(__name__)


# ==========================================
# 1. 纯函数：分带与投影坐标系计算
# ==========================================
def compute_gauss_kruger_zone(
    longitude: float, zone_width: ZoneWidth = 3
) -> Tuple[int, float]:
    """
    根据经度计算高斯克吕格分带号与中央经线。

    - 3 度带：中央经线为 3 的整数倍，带号 = round(longitude / 3)
    - 6 度带：中央经线为 (带号 * 6 - 3)，带号 = floor(longitude / 6) + 1
    """
    if zone_width == 6:
        zone = int(longitude // 6) + 1
        central_meridian = zone * 6 - 3
    else:
        zone = round(longitude / 3)
        central_meridian = zone * 3
    return zone, float(central_meridian)


def compute_cgcs2000_gauss_kruger_epsg(
    longitude: float, zone_width: ZoneWidth = 3
) -> int:
    """
    根据经度和分带宽度计算 CGCS2000 高斯克吕格投影的 EPSG 编码。

    - 6 度带: EPSG 4491-4501 (zone 13-23)
    - 3 度带: EPSG 4513-4533 (CM 75E-135E)
    """
    zone, central_meridian = compute_gauss_kruger_zone(longitude, zone_width)

    if zone_width == 6:
        if not (13 <= zone <= 23):
            logger.warning(
                f"经度 {longitude} 超出 CGCS2000 6 度带常用范围，无法映射 EPSG（zone={zone}）"
            )
        return 4491 + (zone - 13)

    if central_meridian % 3 != 0:
        raise ValueError(
            f"计算得到非法中央经线 {central_meridian}，无法映射 3 度带 EPSG"
        )

    if not (75 <= central_meridian <= 135):
        logger.warning(
            f"经度 {longitude} 超出 CGCS2000 3 度带常用范围，无法映射 EPSG（CM={central_meridian}E）"
        )
    return 4513 + int((central_meridian - 75) // 3)


def build_gauss_kruger_crs(
    longitude: float,
    zone_width: ZoneWidth = 3,
) -> CRS:
    """
    根据经度构建 CGCS2000 高斯克吕格投影坐标系（基于 EPSG 编码）。
    """
    epsg = compute_cgcs2000_gauss_kruger_epsg(longitude, zone_width)
    return CRS.from_epsg(epsg)


# # ==========================================
# # 2. 输入模型定义（供 Tool 调用）
# # ==========================================
# class DetectCRSInput(BaseModel):
#     """
#     坐标系探测工具输入参数模型
#     """

#     model_config = ConfigDict(arbitrary_types_allowed=True)

#     longitude: float = Field(
#         ...,
#         description="用于计算投影坐标系的经度（WGS84 地理坐标，单位：度）。",
#     )
#     latitude: float = Field(
#         0.0,
#         description="用于计算投影坐标系的纬度（WGS84 地理坐标，单位：度），默认 0（仅用于判断南北半球假北坐标）。",
#     )
#     zone_width: ZoneWidth = Field(
#         3,
#         description="高斯克吕格分带宽度，支持 3 度带或 6 度带，默认 3 度带。",
#     )
#     ellipsoid: str = Field(
#         "CGCS2000",
#         description="参考椭球体名称（pyproj 可识别，如 'GRS80'、'WGS84'、'krass'），默认 'CGCS2000'。",
#     )


# # ==========================================
# # 3. 坐标系探测工具核心类
# # ==========================================
# class DetectCRSTool(BaseTool):
#     # 供 Skill 调用的唯一标识
#     name = "detect_crs"

#     # 供 Skill/Agent 语义读取的核心描述（What-When-Output 结构）
#     description = (
#         "Determines the appropriate Gauss-Kruger projected coordinate "
#         "system for a given longitude/latitude location. Use this tool "
#         "when a suitable projected CRS needs to be automatically selected "
#         "for a dataset based on its geographic location, prior to "
#         "reprojection. Returns the zone number, central meridian and the "
#         "resulting CRS (as PROJ4 string) in ToolResult.metadata."
#     )

#     input_model = DetectCRSInput
#     output_model = ToolResult

#     async def execute(self, input_data: DetectCRSInput) -> ToolResult:
#         """
#         原子级坐标系探测核心逻辑：直接调用本模块的纯函数完成分带与投影计算
#         """
#         try:
#             zone, central_meridian = compute_gauss_kruger_zone(
#                 input_data.longitude, input_data.zone_width
#             )
#             crs = build_gauss_kruger_crs(
#                 longitude=input_data.longitude,
#                 latitude=input_data.latitude,
#                 zone_width=input_data.zone_width,
#                 ellipsoid=input_data.ellipsoid,
#             )

#             metadata = {
#                 "zone": zone,
#                 "zone_width": input_data.zone_width,
#                 "central_meridian": central_meridian,
#                 "crs_proj4": crs.to_proj4(),
#             }

#             # 组装统一的成功返回格式
#             return ToolResult(
#                 success=True,
#                 tool_name=self.name,
#                 result_type="crs",
#                 data=None,
#                 metadata=metadata,
#             )

#         except Exception as e:
#             # 捕获异常并统一返回格式
#             return ToolResult(
#                 success=False,
#                 tool_name=self.name,
#                 result_type="crs",
#                 data=None,
#                 error=f"坐标系探测失败: {str(e)}",
#             )
