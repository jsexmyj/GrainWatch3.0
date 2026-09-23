"""
文件相关的原子验证器：
- FileExistsValidator：校验文件路径是否存在
- FileReadableValidator：校验文件是否可读
- DataNotEmptyValidator：校验已加载的数据（GeoDataFrame）是否为空
- ToolExistsValidator：校验指定名称的工具是否已在工具注册表中注册
"""

import os
from pathlib import Path

from ..base import BaseValidator, ValidationContext, ValidationResult


class FileExistsValidator(BaseValidator):
    """校验 file_path 对应的文件是否存在"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if not context.file_path:
            return self._fail("未提供文件路径，无法验证文件是否存在")
        if not Path(context.file_path).exists():
            return self._fail(f"文件不存在：{context.file_path}")
        return self._ok(f"文件存在：{context.file_path}")


class FileReadableValidator(BaseValidator):
    """校验文件是否可读"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if not context.file_path:
            return self._fail("未提供文件路径，无法验证文件是否可读")
        path = Path(context.file_path)
        if not path.is_file():
            return self._fail(f"路径不是一个文件，无法验证可读性：{path}")
        if not os.access(path, os.R_OK):
            return self._fail(f"文件不可读，请检查文件权限：{path}")
        return self._ok(f"文件可读：{path}")


class DataNotEmptyValidator(BaseValidator):
    """校验已加载的数据（GeoDataFrame）是否为空"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if context.gdf is None:
            return self._fail("未提供数据（gdf 为空），无法验证数据是否为空")
        if len(context.gdf) == 0:
            return self._fail("数据为空，不包含任何要素")
        return self._ok(f"数据非空，共 {len(context.gdf)} 条要素")


class ToolExistsValidator(BaseValidator):
    """校验指定名称的工具是否已在工具注册表中注册"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if not context.tool_name:
            return self._fail("未提供工具名称，无法验证工具是否存在")
        if context.tool_registry is None:
            return self._fail("未提供工具注册表，无法验证工具是否存在")
        tool_cls = context.tool_registry.get_tool(context.tool_name)
        if tool_cls is None:
            return self._fail(f"工具不存在：{context.tool_name}")
        return self._ok(f"工具存在：{context.tool_name}")
