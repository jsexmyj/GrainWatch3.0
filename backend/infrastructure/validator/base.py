"""
验证器统一协议：
- ValidationResult：单个验证器的结构化验证结果（Pydantic 数据契约）
- ValidationContext：验证器运行时所需数据的统一载体，各验证器按需读取
- BaseValidator：职责链节点的抽象基类，子类只负责验证一件事，只判断合法性、
  返回问题，不对数据做任何修改
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional, Sequence, Union

import geopandas as gpd
from pydantic import BaseModel, ConfigDict, Field

from backend.utils.logger import get_logger

logger = get_logger(__name__)


class ValidationResult(BaseModel):
    """单个验证器的验证结果，用于记录校验是否通过及原因，便于后续查验"""

    valid: bool = Field(..., description="是否通过验证")
    message: str = Field(default="", description="验证信息（中文），失败时说明具体原因")
    validator_name: str = Field(..., description="产生该结果的验证器名称")
    details: dict[str, Any] = Field(
        default_factory=dict, description="附加详情，例如子验证器的结果列表、失败清单等"
    )


class ValidationContext(BaseModel):
    """
    验证上下文：统一承载各类验证器可能用到的数据。
    各验证器只读取自己关心的字段，未提供的字段视为该验证器不适用，将返回未通过。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    file_path: Optional[Union[str, Path]] = Field(
        default=None, description="待验证的文件路径"
    )

    tool_name: Optional[str] = Field(
        default=None, description="待验证是否存在的工具名称"
    )
    tool_registry: Optional[Any] = Field(
        default=None, description="工具注册表，用于校验工具是否存在"
    )

    gdf: Optional[gpd.GeoDataFrame] = Field(
        default=None, description="待验证的 GeoDataFrame 数据"
    )
    field_names: Optional[Sequence[str]] = Field(
        default=None, description="待验证的字段名列表"
    )

    allowed_geometry_types: Optional[Sequence[str]] = Field(
        default=None, description="度量计算允许的几何类型，例如 Polygon、MultiPolygon"
    )

    extra: dict[str, Any] = Field(
        default_factory=dict, description="扩展字段，供自定义验证器使用"
    )


class BaseValidator(ABC):
    """
    验证器抽象基类（职责链的处理节点）。
    每个子类只负责验证一件事：判断输入是否合法，返回问题描述，不修改任何数据。
    """

    validator_name: str = ""

    def __init__(self, validator_name: Optional[str] = None):
        self.validator_name = (
            validator_name or self.validator_name or self.__class__.__name__
        )

    @abstractmethod
    def validate(self, context: ValidationContext) -> ValidationResult:
        """执行验证逻辑，子类必须实现，仅判断合法性，不修改 context 中的数据"""
        raise NotImplementedError

    def run(self, context: ValidationContext) -> ValidationResult:
        """统一入口：执行验证并在不合法时输出中文错误日志
        适合业务代码直接执行一个验证器时使用。通常比直接调 validate 更合适，因为失败信息会进入日志。"""
        result = self.validate(context)
        if not result.valid:
            logger.error(f"[{result.validator_name}] 验证未通过：{result.message}")
        return result

    def _ok(
        self, message: str = "验证通过", details: Optional[dict[str, Any]] = None
    ) -> ValidationResult:
        return ValidationResult(
            valid=True,
            message=message,
            validator_name=self.validator_name,
            details=details or {},
        )

    def _fail(
        self, message: str, details: Optional[dict[str, Any]] = None
    ) -> ValidationResult:
        return ValidationResult(
            valid=False,
            message=message,
            validator_name=self.validator_name,
            details=details or {},
        )
