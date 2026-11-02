import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
from pydantic import ValidationError

from backend.agent.base_schemas import DataRef
from backend.infrastructure.gis.vector.vector_loader import VectorLoadTool
from backend.infrastructure.tool_manager.base import ToolResult
from backend.infrastructure.tool_manager.manager import ToolManager
from backend.skills.base_schemas import ExecutionResult, ToolPlan, ToolStep
from backend.skills.quantitative_skill.errors import (
    QuantitativeDataError,
    QuantitativeExecutionError,
)
from backend.skills.quantitative_skill.references import (
    iter_references,
    parse_reference,
    plan_dependencies,
)
from backend.skills.quantitative_skill.validation import QuantitativeValidator
from backend.utils.logger import get_logger

logger = get_logger(__name__)
_VECTOR_SUFFIXES = {".shp", ".geojson", ".json"}


@dataclass
class PreparedQuantitativePlan:
    """进程内的数据准备结果，避免用可被 LLM 填写的 metadata 标记跳过验证。"""

    plan: ToolPlan
    warnings: dict[str, list[str]]


class QuantitativeExecutor:
    """按数据依赖并发执行统计步骤，每次调用持有独立缓存和执行上下文。"""

    def __init__(
        self,
        tool_manager: ToolManager,
        validator: QuantitativeValidator | None = None,
        max_concurrency: int = 4,
        offload_tools: bool = True,
    ):
        if max_concurrency < 1:
            raise ValueError("max_concurrency 必须大于零。")
        self.tool_manager = tool_manager
        self.validator = validator or QuantitativeValidator()
        self.max_concurrency = max_concurrency
        self.offload_tools = offload_tools

    async def prepare(
        self, plan: ToolPlan, input_refs: list[DataRef] | None = None
    ) -> PreparedQuantitativePlan:
        """预检全部直接输入；依赖步骤的输出会在实际调用前再次验证。"""
        plan_dependencies(plan)
        inputs = {ref.data_id: ref.data_path for ref in input_refs or []}
        cache: dict[str, gpd.GeoDataFrame] = {}
        steps: list[ToolStep] = []
        preparation_warnings: dict[str, list[str]] = {}
        for step in plan.steps:
            try:
                self.validator.validate_tool(step.tool_name, self.tool_manager.registry)
                if step.on_failure == "retry":
                    raise ValueError("量化统计暂不支持 retry 策略。")
                arguments = await self._prepare_value(step.arguments, inputs, cache)
                warnings: list[str] = []
                if not any(kind == "steps" for kind, _ in iter_references(arguments)):
                    arguments, warnings = await asyncio.to_thread(
                        self.validator.validate_arguments, step.tool_name, arguments
                    )
                    self._validate_input_model(step, arguments)
                # 告警按步骤保存，不把运行时状态写回原始计划。
                steps.append(step.model_copy(update={"arguments": arguments}))
                preparation_warnings[step.step_id] = warnings
                if warnings:
                    logger.warning(
                        "数据预检告警 step_id=%s, warnings=%s", step.step_id, warnings
                    )
            except Exception as exc:
                raise self._wrap_error(step, exc) from exc
        prepared = plan.model_copy(
            update={"steps": steps, "metadata": dict(plan.metadata)}
        )
        logger.info(
            "量化数据准备完成 plan_id=%s, vector_sources=%d", plan.plan_id, len(cache)
        )
        return PreparedQuantitativePlan(prepared, preparation_warnings)

    async def _prepare_value(
        self,
        value: Any,
        inputs: dict[str, str],
        cache: dict[str, gpd.GeoDataFrame],
        key: str | None = None,
    ) -> Any:
        if isinstance(value, dict):
            return {
                name: await self._prepare_value(item, inputs, cache, name)
                for name, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                await self._prepare_value(item, inputs, cache, key) for item in value
            ]
        reference = parse_reference(value)
        if reference:
            kind, identifier = reference
            if kind == "steps":
                return value
            if identifier not in inputs:
                raise ValueError(f"输入引用不存在：{identifier}")
            value = inputs[identifier]
        if key not in {"gdf", "source"} or isinstance(value, gpd.GeoDataFrame):
            return value
        if not isinstance(value, (str, Path)):
            raise QuantitativeDataError(f"{key} 必须是数据路径或 GeoDataFrame。")
        source = self.validator.validate_file(value)
        if key == "gdf" or Path(source).suffix.lower() in _VECTOR_SUFFIXES:
            if source not in cache:
                logger.debug("加载量化矢量输入 source=%s", source)
                # Loader 是内部数据适配工具，不要求 Planner 把它加入统计步骤。
                loader = VectorLoadTool()
                result = await asyncio.to_thread(
                    lambda: asyncio.run(
                        loader.execute(loader.input_model(source=source))
                    )
                )
                if not result.success or not isinstance(result.data, gpd.GeoDataFrame):
                    raise QuantitativeDataError(
                        result.error or "矢量加载未返回 GeoDataFrame。"
                    )
                cache[source] = result.data
            return cache[source]
        return source

    async def execute(
        self,
        plan: ToolPlan | PreparedQuantitativePlan,
        input_refs: list[DataRef] | None = None,
    ) -> ExecutionResult:
        # prepare 可由 Skill 提前调用；这里仍保证 Executor 单独使用时有同样的校验。
        prepared = (
            plan
            if isinstance(plan, PreparedQuantitativePlan)
            else await self.prepare(plan, input_refs)
        )
        plan = prepared.plan
        dependencies = plan_dependencies(plan)
        semaphore = asyncio.Semaphore(self.max_concurrency)
        tasks: dict[str, asyncio.Task] = {}

        async def run_step(step: ToolStep) -> tuple[ToolResult, list[str]]:
            dependency_results = {
                identifier: (await tasks[identifier])[0]
                for identifier in dependencies[step.step_id]
            }
            for identifier, result in dependency_results.items():
                if not result.success:
                    warning = f"step_id={step.step_id}：依赖步骤 {identifier} 失败，跳过本步骤。"
                    logger.warning(warning)
                    return ToolResult(
                        success=False,
                        tool_name=step.tool_name,
                        result_type="skipped",
                        data=None,
                        error=warning,
                    ), [warning]
            try:
                arguments = self._resolve_references(step.arguments, dependency_results)
                async with semaphore:
                    warnings = list(prepared.warnings[step.step_id])
                    if dependencies[step.step_id]:
                        arguments, extra_warnings = await asyncio.to_thread(
                            self.validator.validate_arguments, step.tool_name, arguments
                        )
                        warnings.extend(extra_warnings)
                    self._validate_input_model(step, arguments)
                    # 各工具持有自己的属性表，防止第三方工具修改缓存或依赖输出。
                    arguments = {
                        key: (
                            value.copy()
                            if isinstance(value, gpd.GeoDataFrame)
                            else value
                        )
                        for key, value in arguments.items()
                    }
                    logger.debug(
                        "开始量化步骤 step_id=%s, tool=%s", step.step_id, step.tool_name
                    )
                    if self.offload_tools:
                        # 现有 GIS 工具是 async 接口、同步计算；在线程中运行避免阻塞事件循环。
                        result = await asyncio.to_thread(
                            lambda: asyncio.run(
                                self.tool_manager.execute(step.tool_name, **arguments)
                            )
                        )
                    else:
                        result = await self.tool_manager.execute(
                            step.tool_name, **arguments
                        )
                if not isinstance(result, ToolResult):
                    raise TypeError("统计工具必须返回 ToolResult。")
                if not result.success:
                    category = result.metadata.get("failure_category", "fatal")
                    if category == "data":
                        raise QuantitativeDataError(
                            result.error or "工具返回数据错误。"
                        )
                    if category != "recoverable" or step.on_failure not in {
                        "warn",
                        "continue",
                    }:
                        raise QuantitativeExecutionError(
                            result.error or "工具执行失败。"
                        )
                    warnings.append(result.error or "工具失败但允许继续。")
                tool_warnings = result.metadata.get("warnings") or []
                warnings.extend(
                    [tool_warnings] if isinstance(tool_warnings, str) else tool_warnings
                )
                messages = [
                    f"step_id={step.step_id}, tool={step.tool_name}：{warning}"
                    for warning in warnings
                ]
                for message in messages:
                    logger.warning(message)
                logger.info(
                    "量化步骤完成 step_id=%s, tool=%s, success=%s",
                    step.step_id,
                    step.tool_name,
                    result.success,
                )
                return result, messages
            except Exception as exc:
                logger.error(
                    "量化步骤失败 step_id=%s, tool=%s, reason=%s",
                    step.step_id,
                    step.tool_name,
                    exc,
                )
                raise self._wrap_error(step, exc) from exc

        # 先创建所有任务，再进入事件循环，前向引用也能找到依赖任务。
        for step in plan.steps:
            tasks[step.step_id] = asyncio.create_task(run_step(step))
        try:
            outcomes = await asyncio.gather(
                *(tasks[step.step_id] for step in plan.steps)
            )
        except BaseException:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            raise

        results = [result for result, _ in outcomes]
        warnings = [message for _, messages in outcomes for message in messages]
        context: dict[str, Any] = {}
        failed_step_id = None
        for step, result in zip(plan.steps, results):
            context[step.step_id] = result
            if step.output_key:
                context[step.output_key] = result
            if not result.success and failed_step_id is None:
                failed_step_id = step.step_id
        logger.info(
            "量化计划执行情况:%s, failed_step_id=%s",
            results,
            failed_step_id,
        )
        return ExecutionResult(
            success=all(result.success for result in results),
            results=results,
            context=context,
            failed_step_id=failed_step_id,
            warnings=warnings,
        )

    @staticmethod
    def _resolve_references(value: Any, results: dict[str, ToolResult]) -> Any:
        if isinstance(value, dict):
            return {
                key: QuantitativeExecutor._resolve_references(item, results)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                QuantitativeExecutor._resolve_references(item, results)
                for item in value
            ]
        reference = parse_reference(value)
        if reference:
            kind, identifier = reference
            if kind != "steps" or identifier not in results:
                raise ValueError(f"未解析的数据引用：{value}")
            return results[identifier].data
        return value

    def _validate_input_model(self, step: ToolStep, arguments: dict[str, Any]) -> None:
        model = self.tool_manager.registry.get_tool(step.tool_name).input_model
        unknown = arguments.keys() - model.model_fields.keys()
        if unknown:
            raise ValueError(f"工具参数不存在：{sorted(unknown)}")
        model.model_validate(arguments)

    @staticmethod
    def _wrap_error(step: ToolStep, exc: Exception) -> QuantitativeExecutionError:
        if isinstance(exc, QuantitativeExecutionError):
            error_type = (
                QuantitativeDataError
                if exc.category == "data"
                else QuantitativeExecutionError
            )
            return error_type(
                exc.reason, step_id=step.step_id, tool_name=step.tool_name
            )
        if isinstance(exc, (OSError, KeyError)):
            return QuantitativeDataError(
                str(exc), step_id=step.step_id, tool_name=step.tool_name
            )
        reason = (
            f"参数验证失败：{exc}"
            if isinstance(exc, ValidationError)
            else f"{type(exc).__name__}: {exc}"
        )
        return QuantitativeExecutionError(
            reason, step_id=step.step_id, tool_name=step.tool_name
        )
