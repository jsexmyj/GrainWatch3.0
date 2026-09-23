"""
验证流水线：以组合模式自由编排多个 BaseValidator，
按注册顺序依次执行（职责链），并汇总所有验证结果。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .base import BaseValidator, ValidationContext, ValidationResult


class PipelineResult(BaseModel):
    """流水线聚合验证结果"""

    valid: bool = Field(..., description="是否所有验证器均通过")
    results: List[ValidationResult] = Field(
        default_factory=list, description="各验证器的详细结果"
    )

    @property
    def errors(self) -> List[ValidationResult]:
        """仅返回未通过的验证结果"""
        return [r for r in self.results if not r.valid]


class ValidationPipeline(BaseValidator):
    """
    验证流水线（组合模式）：按顺序持有若干 BaseValidator 并逐一执行。
    自身同样实现 BaseValidator 协议，因此可作为子节点嵌套进另一个流水线。
    """

    def __init__(
        self,
        validators: Optional[List[BaseValidator]] = None,
        stop_on_first_failure: bool = False,
        validator_name: str = "ValidationPipeline",
    ):
        super().__init__(validator_name=validator_name)
        self._validators: List[BaseValidator] = list(validators or [])
        self.stop_on_first_failure = stop_on_first_failure

    def add(self, validator: BaseValidator) -> "ValidationPipeline":
        """向流水线追加验证器，支持链式调用"""
        self._validators.append(validator)
        return self

    def run_all(self, context: ValidationContext) -> PipelineResult:
        """依次执行所有验证器并汇总结果；stop_on_first_failure 为 True 时，遇到失败即中断
        内部遍历子验证器并调用每个子验证器的 run，所以每个失败项都会写日志
        针对日志和个人检查场景，返回详细执行结果
        适合 API 需要把每一项失败原因都返回给前端、调试、测试验证器组合等场景。"""
        results: List[ValidationResult] = []
        for validator in self._validators:
            result = validator.run(context)
            results.append(result)
            if not result.valid and self.stop_on_first_failure:
                break
        return PipelineResult(valid=all(r.valid for r in results), results=results)

    def validate(self, context: ValidationContext) -> ValidationResult:
        """实现 BaseValidator 协议：将整条流水线的结果聚合为单条 ValidationResult
        对外统一的接口，每个 BaseValidator 必须实现它。它不记录日志，只回答“是否有效，以及为什么。
        适合单元测试，或者你不希望失败时产生错误日志的内部调用。"""
        pipeline_result = self.run_all(context)
        if pipeline_result.valid:
            return self._ok(
                "流水线中所有验证均通过", details={"results": pipeline_result.results}
            )
        return self._fail(
            "流水线验证失败",
            details={
                "errors": pipeline_result.errors,
                "results": pipeline_result.results,
            },
        )
