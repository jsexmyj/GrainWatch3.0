from typing import Any

from .base import ToolResult
from .registry import ToolRegistry


class ToolManager:
    """
    对外的统一调度器，负责：
    1. 寻找已注册的工具
    2. 使用 Pydantic 进行输入参数校验
    3. 执行异步逻辑并返回结果
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    async def execute(self, tool_name: str, **kwargs: Any) -> Any:
        """
        统一执行接口
        """
        tool_cls = self.registry.get_tool(tool_name)
        if not tool_cls:
            raise ValueError(
                f"未找到名为 '{tool_name}' 的工具，请检查工具是否已正常载入。"
            )

        # 实例化工具
        tool_instance = tool_cls()

        # 1. 输入数据验证（如果不符合 input_model 定义，Pydantic 会在此抛出 ValidationError）
        validated_input = tool_instance.input_model(**kwargs)

        # 2. 执行核心计算
        output_data = await tool_instance.execute(validated_input)

        # 3. 统一补齐事实语句，避免上游重复拼装工具语义。
        if isinstance(output_data, ToolResult) and not output_data.fact:
            output_data.fact = tool_instance.build_fact(output_data)

        # 4. 输出数据保证符合 output_model
        return output_data
