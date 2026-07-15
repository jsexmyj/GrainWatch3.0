import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.infrastructure.tool_manager.loader import ToolLoader
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.infrastructure.tool_manager.registry import ToolRegistry
from backend.utils.paths import PATHS


async def test_vector_load_and_buffer():
    # 1. 自动载入工具
    registry = ToolRegistry()
    ToolLoader.auto_load_tools(
        registry=registry, package_path=PATHS.infrastructure_path("gis")
    )
    tool_manager = ToolManager(registry)

    # 定义测试数据路径
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shp_path = os.path.join(
        project_root, "data", "shp数据", "范围线_4544", "研究区范围_Project.shp"
    )
    output_dir = os.path.join(project_root, "data", "运行结果")
    os.makedirs(output_dir, exist_ok=True)
    buffer_shp_path = os.path.join(output_dir, "buffer.shp")

    print(f"\n{'='*60}")
    print(f"步骤 1/3: 加载 Shapefile 数据")
    print(f"{'='*60}")

    try:
        load_result = await tool_manager.execute(
            tool_name="vector_load",
            source=shp_path,
            source_type="shp",
        )

        if not load_result.success:
            print(f"✗ 数据加载失败")
            print(f"  错误: {load_result.error}")
            return

        gdf = load_result.data
        print(f"✓ 数据加载成功")
        print(f"  结果类型: {load_result.result_type}")
        print(f"  数据类型: {type(gdf).__name__}")
        print(f"  要素数量: {len(gdf)}")
        print(f"  几何类型: {gdf.geometry.geom_type.unique().tolist()}")
        print(f"  坐标系: {gdf.crs}")
        print(f"  边界范围: {gdf.total_bounds.tolist()}")
        print(f"  列字段: {list(gdf.columns)}")

        if load_result.metadata:
            print(f"  元数据:")
            for key, value in load_result.metadata.items():
                if key not in ["columns"]:
                    print(f"    - {key}: {value}")
    except Exception as e:
        print(f"✗ 数据加载异常发生: {type(e).__name__}")
        print(f"  错误信息: {str(e)}")
        return

    print(f"\n{'='*60}")
    print(f"步骤 2/3: 执行 buffer=10")
    print(f"{'='*60}")

    try:
        buffer_result = await tool_manager.execute(
            tool_name="buffer",
            gdf=gdf,
            distance=10,
        )

        if not buffer_result.success:
            print(f"✗ buffer 执行失败")
            print(f"  错误: {buffer_result.error}")
            return

        buffered_gdf = buffer_result.data
        print(f"✓ buffer 执行成功")
        print(f"  结果类型: {buffer_result.result_type}")
        print(f"  数据类型: {type(buffered_gdf).__name__}")
        print(f"  要素数量: {len(buffered_gdf)}")
        print(f"  几何类型: {buffered_gdf.geometry.geom_type.unique().tolist()}")
        print(f"  坐标系: {buffered_gdf.crs}")
        print(f"  边界范围: {buffered_gdf.total_bounds.tolist()}")
        print(f"  列字段: {list(buffered_gdf.columns)}")

        if buffer_result.metadata:
            print(f"  元数据:")
            for key, value in buffer_result.metadata.items():
                print(f"    - {key}: {value}")
    except Exception as e:
        print(f"✗ buffer 异常发生: {type(e).__name__}")
        print(f"  错误信息: {str(e)}")
        return

    print(f"\n{'='*60}")
    print(f"步骤 3/3: 导出为 Shapefile: {buffer_shp_path}")
    print(f"{'='*60}")

    try:
        write_result = await tool_manager.execute(
            tool_name="vector_write",
            gdf=buffered_gdf,
            destination=buffer_shp_path,
            target_type="shp",
        )

        if not write_result.success:
            print(f"✗ 导出失败")
            print(f"  错误: {write_result.error}")
            return

        file_resource = write_result.data
        print(f"✓ 导出成功")
        print(f"  结果类型: {write_result.result_type}")
        print(
            f"  文件路径: {file_resource.path if hasattr(file_resource, 'path') else buffer_shp_path}"
        )
        print(f"  文件格式: shp")

        if write_result.metadata:
            print(f"  元数据:")
            for key, value in write_result.metadata.items():
                print(f"    - {key}: {value}")
    except Exception as e:
        print(f"✗ 导出异常发生: {type(e).__name__}")
        print(f"  错误信息: {str(e)}")
        return

    print(f"\n{'='*60}")
    print(f"全部流程执行完成")
    print(f"{'='*60}")


asyncio.run(test_vector_load_and_buffer())
