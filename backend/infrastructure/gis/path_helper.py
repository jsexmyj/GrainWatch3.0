# backend/infrastructure/gis/path_helper.py
from pathlib import Path
from typing import Optional, Union

# 导入全局路径管理器
from backend.utils.paths import PATHS

def resolve_gis_output_path(
    destination: Optional[Union[str, Path]] = None,
    file_name: Optional[str] = None,
    source_path: Optional[Union[str, Path]] = None,
    tool_suffix: str = "output",
    extension: str = "tif",
    auto_increment: bool = True
) -> str:
    """
    GIS 统一输出路径解析器，基于系统的 PATHS 进行物理路径定位
    """
    # 1. 确定目标文件夹和初始文件名
    if destination:
        dest_path = Path(destination)
        target_dir = dest_path.parent
        target_name = dest_path.stem
        ext = dest_path.suffix.lstrip('.') or extension
    else:
        # 利用 PATHS 获取标准的 "data/运行结果" 目录，PATHS 会自动创建该目录
        target_dir = PATHS.data_path("运行结果", mkdir=True)
        ext = extension
        
        if file_name:
            target_name = file_name
        elif source_path:
            # 策略：原文件名_工具名
            source_stem = Path(source_path).stem
            target_name = f"{source_stem}_{tool_suffix}"
        else:
            # 策略：默认工具名
            target_name = f"{tool_suffix}_output"

    # 2. 冲突递增策略 (避免覆盖)
    final_path = target_dir / f"{target_name}.{ext}"
    if auto_increment and final_path.exists():
        counter = 1
        while True:
            new_path = target_dir / f"{target_name}({counter}).{ext}"
            if not new_path.exists():
                final_path = new_path
                break
            counter += 1

    return str(final_path.resolve())