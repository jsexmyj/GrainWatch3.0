import asyncio
import geopandas as gpd
from shapely.geometry import Point
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.infrastructure.tool_manager.loader import ToolLoader
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.utils.paths import PATHS


async def run_skill_workflow():
    # 1. 自动载入工具
    registry = ToolRegistry()
    ToolLoader.auto_load_tools(
        registry=registry, package_path=PATHS.infrastructure_path("gis", "vector")
    )
    tool_manager = ToolManager(registry)

    # 2. 模拟前置工具已处理完投影转换，生成了一个处于投影系（如 EPSG:3857）下的内存 GeoDataFrame
    mock_point = Point(12941217.0, 4851253.0)  # 北京坐标在 3857 投影下的近似米制单位
    input_gdf = gpd.GeoDataFrame(geometry=[mock_point], crs="EPSG:3857")

    # 3. 调用原子级 Buffer 核心工具（纯内存传递）
    print("Skill 开始调用原子 buffer 工具...")

    result = await tool_manager.execute(
        tool_name="buffer",
        gdf=input_gdf,  # 直接传入内存中的 GeoDataFrame
        distance=500.0,  # 缓冲 500 米
        quad_segs=16,
    )

    # 4. 读取标准输出
    if result.success:
        output_gdf: gpd.GeoDataFrame = result.data
        print(f"执行成功！结果类型: {result.result_type}")
        print(f"输出数据类型: {type(output_gdf)}")
        print(f"缓冲后的几何体: {output_gdf.geometry.iloc[0]}")
    else:
        print(f"执行失败，错误信息: {result.error}")


asyncio.run(run_skill_workflow())
