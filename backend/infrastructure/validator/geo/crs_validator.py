"""坐标系相关的原子验证器：CRSNotNullValidator 校验坐标参考系是否为空"""

from ..base import BaseValidator, ValidationContext, ValidationResult


class CRSNotNullValidator(BaseValidator):
    """校验 gdf 是否已设置坐标参考系（CRS）"""

    def validate(self, context: ValidationContext) -> ValidationResult:
        if context.gdf is None:
            return self._fail("未提供数据（gdf 为空），无法验证坐标系是否为空")
        if context.gdf.crs is None:
            return self._fail("数据缺少坐标参考系（CRS）")
        return self._ok(f"坐标参考系存在：{context.gdf.crs.to_string()}")
