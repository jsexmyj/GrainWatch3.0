import asyncio
import re
from typing import Any

from pydantic import ValidationError

from backend.infrastructure.tool_manager.base import ToolResult
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.skills.spatial_skill.schemas import ExecutionResult, ToolPlan, ToolStep
from backend.utils.logger import get_logger

_STEP_REFERENCE = re.compile(r"^\$\{steps\.([^.}]+)\.data\}$")

logger = get_logger(__name__)

_NETWORK_ERROR_KEYWORDS = (
    "timeout",
    "timed out",
    "network",
    "connection",
    "连接",
    "超时",
    "temporarily unavailable",
    "service unavailable",
    "503",
    "502",
    "504",
    "reset by peer",
)


class SpatialExecutionError(RuntimeError):
    """SpatialExecutor 统一抛出的上层异常基类。"""


class ToolStepExecutionError(SpatialExecutionError):
    """单个工具步骤执行失败，统一包装给上层调用方处理。"""

    def __init__(
        self,
        *,
        step_id: str,
        tool_name: str,
        category: str,
        reason: str,
    ):
        self.step_id = step_id
        self.tool_name = tool_name
        self.category = category
        self.reason = reason
        super().__init__(
            f"步骤执行失败 step_id={step_id}, tool={tool_name}, "
            f"category={category}, reason={reason}"
        )


class SpatialExecutor:
    """确定性地执行 ToolPlan，并将每步结果写入运行时上下文。"""

    def __init__(self, tool_manager: ToolManager):
        self.tool_manager = tool_manager
        self.max_network_retries = 2
        self.retry_backoff_seconds = 0.5

    async def execute(self, plan: ToolPlan) -> ExecutionResult:
        context: dict[str, Any] = {}
        results: list[ToolResult] = []
        warnings: list[str] = []
        failed_step_id: str | None = None

        logger.debug(
            "开始执行空间工具计划 plan_id=%s, steps=%d", plan.plan_id, len(plan.steps)
        )

        for step in plan.steps:
            logger.debug(
                "开始执行步骤 step_id=%s, tool=%s, on_failure=%s, arguments=%s",
                step.step_id,
                step.tool_name,
                step.on_failure,
                self._safe_log_value(step.arguments),
            )

            try:
                # 先把参数中的占位符替换成上一步真实输出，再执行当前工具。
                arguments = self._resolve_references(step.arguments, context)
            except Exception as exc:
                reason = f"参数引用解析失败: {exc}"
                logger.error(
                    "步骤失败 step_id=%s, tool=%s, 原因=%s",
                    step.step_id,
                    step.tool_name,
                    reason,
                )
                raise ToolStepExecutionError(
                    step_id=step.step_id,
                    tool_name=step.tool_name,
                    category="fatal",
                    reason=reason,
                ) from exc

            result = await self._execute_with_policy(step, arguments)
            results.append(result)
            logger.info(
                "步骤执行结果 step_id=%s, result=%s",
                step.step_id,
                result.model_dump_json(indent=2, exclude={"data"}),
            )

            # 同时保留 step_id 与 output_key 两种索引，便于后续引用。
            context[step.step_id] = result
            if step.output_key:
                context[step.output_key] = result

            if not result.success:
                failed_step_id = step.step_id
                warning_message = (
                    f"step_id={step.step_id}, tool={step.tool_name}, "
                    f"原因={result.error or '未知错误'}"
                )
                if step.on_failure == "warn":
                    warnings.append(warning_message)
                    logger.warning("步骤失败但继续执行 %s", warning_message)
                    continue

                logger.error(
                    "步骤失败并中止 plan_id=%s, %s", plan.plan_id, warning_message
                )
                raise ToolStepExecutionError(
                    step_id=step.step_id,
                    tool_name=step.tool_name,
                    category="fatal",
                    reason=result.error or "工具返回失败",
                )

        return ExecutionResult(
            success=all(result.success for result in results),
            results=results,
            context=context,
            failed_step_id=failed_step_id,
            warnings=warnings,
        )

    async def _execute_with_policy(
        self, step: ToolStep, arguments: dict[str, Any]
    ) -> ToolResult:
        max_attempts = 1 + self.max_network_retries
        attempt = 0

        while attempt < max_attempts:
            attempt += 1
            try:
                result = await self.tool_manager.execute(step.tool_name, **arguments)
            except Exception as exc:
                category = self._classify_exception(exc)
                reason = f"{type(exc).__name__}: {exc}"

                if (
                    category == "retryable"
                    and step.on_failure == "retry"
                    and attempt < max_attempts
                ):
                    logger.warning(
                        "网络可恢复错误，准备重试 step_id=%s, tool=%s, attempt=%d/%d, 错误=%s",
                        step.step_id,
                        step.tool_name,
                        attempt,
                        max_attempts,
                        reason,
                    )
                    await asyncio.sleep(self.retry_backoff_seconds * attempt)
                    continue

                logger.error(
                    "步骤异常 step_id=%s, tool=%s, category=%s, attempt=%d/%d, 错误=%s",
                    step.step_id,
                    step.tool_name,
                    category,
                    attempt,
                    max_attempts,
                    reason,
                )
                raise ToolStepExecutionError(
                    step_id=step.step_id,
                    tool_name=step.tool_name,
                    category=category,
                    reason=reason,
                ) from exc

            if result.success:
                return result

            category = self._classify_result_error(result.error)
            reason = result.error or "工具返回失败"

            if (
                category == "retryable"
                and step.on_failure == "retry"
                and attempt < max_attempts
            ):
                logger.warning(
                    "工具返回可恢复错误，准备重试 step_id=%s, tool=%s, attempt=%d/%d, 错误=%s",
                    step.step_id,
                    step.tool_name,
                    attempt,
                    max_attempts,
                    reason,
                )
                await asyncio.sleep(self.retry_backoff_seconds * attempt)
                continue

            return result

        return ToolResult(
            success=False,
            tool_name=step.tool_name,
            result_type="unknown",
            data=None,
            fact=f"工具 {step.tool_name} 在达到最大重试次数后仍执行失败。",
            error="达到最大重试次数后仍失败",
        )

    @staticmethod
    def _classify_exception(exc: Exception) -> str:
        message = str(exc).lower()
        if any(keyword in message for keyword in _NETWORK_ERROR_KEYWORDS):
            return "retryable"
        if isinstance(exc, (ValidationError, ValueError, TypeError, KeyError)):
            return "fatal"
        return "fatal"

    @staticmethod
    def _classify_result_error(error: str | None) -> str:
        if not error:
            return "fatal"
        message = error.lower()
        if any(keyword in message for keyword in _NETWORK_ERROR_KEYWORDS):
            return "retryable"
        if any(
            keyword in message
            for keyword in ("缺少", "missing", "required", "参数", "mismatch", "不匹配")
        ):
            return "fatal"
        return "fatal"

    @staticmethod
    def _safe_log_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: SpatialExecutor._safe_log_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [SpatialExecutor._safe_log_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return f"<{type(value).__name__}>"

    def _resolve_references(self, value: Any, context: dict[str, Any]) -> Any:
        """核心是把字符串中的占位符替换为对应的结果值。"""
        if isinstance(value, str):
            matched = _STEP_REFERENCE.fullmatch(value)
            if not matched:
                return value
            step_id = matched.group(1)
            result = context.get(step_id)
            if not isinstance(result, ToolResult):
                raise ValueError(f"无法解析步骤结果引用: {value}")
            return result.data
        if isinstance(value, dict):
            return {
                key: self._resolve_references(item, context)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_references(item, context) for item in value]
        return value
