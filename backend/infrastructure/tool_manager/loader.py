from pathlib import Path
import sys
from typing import Union

from backend.utils.paths import PATHS
from .base import BaseTool
from .registry import ToolRegistry
import importlib
import pkgutil
import inspect


class ToolLoader:
    """
    自动扫描指定包（目录）下的模块并注册工具
    """

    @staticmethod
    def auto_load_tools(
        registry: ToolRegistry, package_path: Union[Path, str] = PATHS.infrastructure
    ):
        """
        动态导入指定包，并自动注册继承了 BaseTool 的类
        """
        # 1. 检测是否为物理文件系统路径（如果是 Path 对象，或字符串中含有路径分隔符）
        is_file_path = False
        path_obj = None

        if isinstance(package_path, Path):
            is_file_path = True
            path_obj = package_path
        elif isinstance(package_path, str):
            # 如果字符串中包含路径斜杠，或该路径在文件系统中确实存在，则视为物理路径
            if "/" in package_path or "\\" in package_path or Path(package_path).exists():
                is_file_path = True
                path_obj = Path(package_path)

        # 2. 策略 A：如果是物理文件路径（Path 对象），直接扫描 .py 文件进行物理加载
        if is_file_path and path_obj is not None:
            if not path_obj.exists():
                raise FileNotFoundError(f"指定的工具加载路径不存在: {path_obj}")

            # 递归扫描目录下所有 .py 文件（排除 __init__.py）
            for file_path in path_obj.rglob("*.py"):
                if file_path.name == "__init__.py":
                    continue
                
                try:
                    module_name = file_path.stem
                    # 利用 importlib.util 直接从物理文件路径加载模块
                    spec = importlib.util.spec_from_file_location(module_name, file_path)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        # 注入 sys.modules 防止重复加载或相对导入冲突
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)

                        # 检查并注册继承了 BaseTool 的类
                        for _, obj in inspect.getmembers(module, inspect.isclass):
                            if issubclass(obj, BaseTool) and obj is not BaseTool:
                                registry.register(obj)
                except Exception as e:
                    # 打印警告并继续加载其他工具，保证整个系统不因单个工具的代码语法错误而崩溃
                    print(f"[警告] 无法从文件 {file_path} 加载工具: {e}")

        # 3. 策略 B：如果是标准的 Python 模块点号命名空间字符串（如 "infrastructure.tools"）
        else:
            package_name = str(package_path)
            try:
                import pkgutil
                package = importlib.import_module(package_name)
                
                for _, module_name, is_pkg in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
                    if not is_pkg:
                        module = importlib.import_module(module_name)
                        for _, obj in inspect.getmembers(module, inspect.isclass):
                            if issubclass(obj, BaseTool) and obj is not BaseTool:
                                registry.register(obj)
            except Exception as e:
                raise ImportError(f"无法通过模块命名空间 {package_name} 自动加载工具: {e}")