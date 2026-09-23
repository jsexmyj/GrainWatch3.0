"""几何相关的原子验证器：GeometryNotEmptyValidator 校验几何列是否存在且不为空"""

from ..base import BaseValidator, ValidationContext, ValidationResult


class GeometryNotEmptyValidator(BaseValidator):
    """校验 gdf 是否包含 geometry 列，且不存在缺失或空几何"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if context.gdf is None:
            return self._fail("未提供数据（gdf 为空），无法验证几何是否为空")
        if "geometry" not in context.gdf.columns:
            return self._fail("数据不包含 geometry 列")

        geometry = context.gdf.geometry
        missing_count = int(geometry.isna().sum() + geometry.is_empty.sum())
        if missing_count > 0:
            return self._fail(f"存在 {missing_count} 个空几何或缺失几何")
        return self._ok("几何均非空")
