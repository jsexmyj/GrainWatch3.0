from abc import ABC, abstractmethod
from typing import Any, Type
from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    tool_name: str = Field(description="工具名称")
    result_type: str = Field(description="结果类型")
    data: Any = Field(description="结果数据")
    metadata: dict = Field(default={}, description="元数据")
    error: str | None = Field(default=None, description="错误信息")


class BaseTool(ABC):
    """
    所有工具的抽象基类。
    """

    name: str
    description: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel] = ToolResult

    @abstractmethod
    async def execute(self, input_data: BaseModel) -> ToolResult:
        """
        子类必须实现此异步方法以执行具体的业务逻辑。
        """
        pass
