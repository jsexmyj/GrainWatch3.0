import importlib
import pkgutil
import inspect
from typing import Dict, Type
from .base import BaseTool

class ToolRegistry:
    """
    负责存储和检索工具类
    """
    def __init__(self):
        self._tools: Dict[str, Type[BaseTool]] = {}

    def register(self, tool_cls: Type[BaseTool]):
        """手动注册工具"""
        if not hasattr(tool_cls, 'name') or not tool_cls.name:
            raise ValueError(f"工具类 {tool_cls.__name__} 必须定义 'name' 属性。")
        self._tools[tool_cls.name] = tool_cls

    def get_tool(self, name: str) -> Type[BaseTool]:
        """获取工具类"""
        return self._tools.get(name)

    def list_all_tools(self) -> Dict[str, str]:
        """获取所有已注册工具的名称及描述"""
        return {name: cls.description for name, cls in self._tools.items()}

