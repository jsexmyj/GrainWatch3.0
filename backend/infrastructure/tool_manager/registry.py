import inspect
import json
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, Dict, Type, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import PydanticUndefined
from pydantic.json_schema import PydanticInvalidForJsonSchema

from .base import BaseTool

_STEP_DATA_REFERENCE_SCHEMA = {
    "type": "string",
    "pattern": r"^\$\{steps\.[^.}]+\.data\}$",
    "description": "引用上一个工具步骤的输出数据，格式必须为 ${steps.<step_id>.data}。",
}

_JSON_SCALAR_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


class ToolRegistry:
    """
    负责存储和检索工具类
    """

    def __init__(self):
        self._tools: Dict[str, Type[BaseTool]] = {}

    def register(self, tool_cls: Type[BaseTool]):
        """手动注册工具"""
        if not hasattr(tool_cls, "name") or not tool_cls.name:
            raise ValueError(f"工具类 {tool_cls.__name__} 必须定义 'name' 属性。")
        self._tools[tool_cls.name] = tool_cls

    def get_tool(self, name: str) -> Type[BaseTool]:
        """获取工具类"""
        return self._tools.get(name)

    def list_all_tools(self) -> Dict[str, str]:
        """获取所有已注册工具的名称及描述"""
        return {name: cls.description for name, cls in self._tools.items()}

    def list_tool_catalog(
        self,
        tool_directory: (
            str | Path | list[str | Path] | tuple[str | Path, ...] | None
        ) = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        获取工具目录，包含名称、描述、输入 schema。

        参数:
        - tool_directory: 仅返回指定目录下定义的工具；为空则返回所有已注册工具。
        """
        roots = []
        if tool_directory:
            directories = (
                tool_directory
                if isinstance(tool_directory, (list, tuple))
                else [tool_directory]
            )
            roots = [Path(directory).resolve() for directory in directories]
        catalog: Dict[str, Dict[str, Any]] = {}

        for name, tool_cls in self._tools.items():
            source_file = self._get_tool_source_file(tool_cls)
            if roots and source_file:
                if not any(
                    self._is_relative_to(source_file.resolve(), root) for root in roots
                ):
                    continue
            elif roots and not source_file:
                # 无法定位源文件时，不参与目录过滤结果。
                continue

            schema = self._safe_model_json_schema(tool_cls)
            catalog[name] = {
                "name": name,
                "description": getattr(tool_cls, "description", ""),
                "input_schema": schema,
                "module": tool_cls.__module__,
                "source_file": str(source_file) if source_file else None,
            }

        return catalog

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _get_tool_source_file(tool_cls: Type[BaseTool]) -> Path | None:
        source = inspect.getsourcefile(tool_cls)
        if source:
            return Path(source)
        module = inspect.getmodule(tool_cls)
        module_file = getattr(module, "__file__", None)
        return Path(module_file) if module_file else None

    @staticmethod
    def _safe_model_json_schema(tool_cls: Type[BaseTool]) -> dict[str, Any]:
        """为 planner 生成隔离后的输入 schema，复杂运行时类型降级为步骤引用。"""
        model = getattr(tool_cls, "input_model", None)
        if model is None:
            return {}
        try:
            raw_schema = model.model_json_schema()
            return json.loads(json.dumps(raw_schema, ensure_ascii=False, default=str))
        except PydanticInvalidForJsonSchema:
            return ToolRegistry._build_planner_input_schema(model)

    @staticmethod
    def _build_planner_input_schema(model: Type[BaseModel]) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []

        for field_name, field_info in model.model_fields.items():
            field_schema = ToolRegistry._annotation_to_planner_schema(
                field_info.annotation
            )
            if field_info.description:
                field_schema["description"] = field_info.description

            default = ToolRegistry._get_json_safe_default(field_info)
            if default is not PydanticUndefined:
                field_schema["default"] = default

            properties[field_name] = field_schema
            if field_info.is_required():
                required.append(field_name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "title": model.__name__,
        }
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _annotation_to_planner_schema(annotation: Any) -> dict[str, Any]:
        '''将运行时类型转换为 planner 输入 schema。'''
        if annotation is Any:
            return {}

        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin in (UnionType, getattr(__import__("typing"), "Union")):
            non_none_args = [arg for arg in args if arg is not type(None)]
            if len(non_none_args) == 1:
                return ToolRegistry._annotation_to_planner_schema(non_none_args[0])
            return {
                "anyOf": [
                    ToolRegistry._annotation_to_planner_schema(arg)
                    for arg in non_none_args
                ]
            }

        if origin is not None:
            if origin in (list, tuple, set):
                item_annotation = args[0] if args else Any
                return {
                    "type": "array",
                    "items": ToolRegistry._annotation_to_planner_schema(
                        item_annotation
                    ),
                }
            if origin is dict:
                value_annotation = args[1] if len(args) > 1 else Any
                additional_properties = ToolRegistry._annotation_to_planner_schema(
                    value_annotation
                )
                return {
                    "type": "object",
                    "additionalProperties": additional_properties or True,
                }

        if annotation in _JSON_SCALAR_TYPES:
            return {"type": _JSON_SCALAR_TYPES[annotation]}

        if annotation in (Path,):
            return {"type": "string"}

        if inspect.isclass(annotation) and issubclass(annotation, Enum):
            enum_values = [member.value for member in annotation]
            schema: dict[str, Any] = {"enum": enum_values}
            if enum_values:
                value_type = type(enum_values[0])
                if value_type in _JSON_SCALAR_TYPES:
                    schema["type"] = _JSON_SCALAR_TYPES[value_type]
            return schema

        if origin is not None and str(origin).endswith("Literal"):
            literal_values = list(args)
            schema = {"enum": literal_values}
            if literal_values:
                value_type = type(literal_values[0])
                if value_type in _JSON_SCALAR_TYPES:
                    schema["type"] = _JSON_SCALAR_TYPES[value_type]
            return schema

        if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
            return ToolRegistry._build_planner_input_schema(annotation)

        return dict(_STEP_DATA_REFERENCE_SCHEMA)

    @staticmethod
    def _get_json_safe_default(field_info: Any) -> Any:
        default = field_info.get_default(call_default_factory=True)
        if default is PydanticUndefined:
            return PydanticUndefined
        try:
            json.dumps(default, ensure_ascii=False, default=str)
        except TypeError:
            return PydanticUndefined
        return default
