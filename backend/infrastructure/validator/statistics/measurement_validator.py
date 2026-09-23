"""
度量类验证器：
- MeasurementValidator：校验待计算长度/面积的数据是否具备所需的几何类型，
  以及坐标参考系是否允许直接进行长度/面积计算（须为投影坐标系）。
"""

from ..base import BaseValidator, ValidationContext, ValidationResult


class MeasurementValidator(BaseValidator):
    """校验几何类型是否符合度量要求，以及 CRS 是否允许直接计算长度/面积"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if context.gdf is None:
            return self._fail("未提供数据（gdf 为空），无法验证度量前置条件")

        if context.allowed_geometry_types:
            geom_types = set(context.gdf.geometry.geom_type.unique())
            allowed = set(context.allowed_geometry_types)
            invalid_types = geom_types - allowed
            if invalid_types:
                return self._fail(
                    f"存在不支持的几何类型：{'、'.join(invalid_types)}，"
                    f"度量计算仅支持：{'、'.join(allowed)}"
                )

        crs = context.gdf.crs
        if crs is None:
            return self._fail("数据缺少坐标参考系（CRS），无法进行长度/面积计算")
        if not crs.is_projected:
            return self._fail(
                "坐标参考系为地理坐标系，无法直接进行长度/面积计算，请先投影"
            )

        return self._ok("几何类型与坐标参考系均满足度量计算要求")
