"""
字段相关的原子验证器：
- FieldExistsValidator：校验指定字段是否存在于 GeoDataFrame 中
- FieldNotAllNullValidator：校验指定字段是否全部为空值
- FieldNumericValidator：校验指定字段是否为数值类型
"""

import pandas as pd

from ..base import BaseValidator, ValidationContext, ValidationResult


class FieldExistsValidator(BaseValidator):
    """校验 field_names 中的字段是否都存在于 gdf 中"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if context.gdf is None:
            return self._fail("未提供数据（gdf 为空），无法验证字段是否存在")
        if not context.field_names:
            return self._fail("未提供待验证的字段名，无法验证字段是否存在")

        missing = [f for f in context.field_names if f not in context.gdf.columns]
        if missing:
            return self._fail(f"字段不存在：{'、'.join(missing)}")
        return self._ok(f"字段均存在：{'、'.join(context.field_names)}")


class FieldNotAllNullValidator(BaseValidator):
    """校验 field_names 中的字段是否全部为空值"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if context.gdf is None:
            return self._fail("未提供数据（gdf 为空），无法验证字段是否全为空")
        if not context.field_names:
            return self._fail("未提供待验证的字段名，无法验证字段是否全为空")

        all_null_fields = []
        for field in context.field_names:
            if field not in context.gdf.columns:
                return self._fail(f"字段不存在，无法验证是否全为空：{field}")
            if context.gdf[field].isna().all():
                all_null_fields.append(field)

        if all_null_fields:
            return self._fail(f"字段全部为空值：{'、'.join(all_null_fields)}")
        return self._ok(f"字段均存在有效值：{'、'.join(context.field_names)}")


class FieldNumericValidator(BaseValidator):
    """校验 field_names 中的字段是否均为数值类型"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if context.gdf is None:
            return self._fail("未提供数据（gdf 为空），无法验证字段类型")
        if not context.field_names:
            return self._fail("未提供待验证的字段名，无法验证字段类型")

        non_numeric_fields = []
        for field in context.field_names:
            if field not in context.gdf.columns:
                return self._fail(f"字段不存在，无法验证是否为数值类型：{field}")
            if not pd.api.types.is_numeric_dtype(context.gdf[field]):
                non_numeric_fields.append(field)

        if non_numeric_fields:
            return self._fail(f"字段不是数值类型：{'、'.join(non_numeric_fields)}")
        return self._ok(f"字段均为数值类型：{'、'.join(context.field_names)}")
