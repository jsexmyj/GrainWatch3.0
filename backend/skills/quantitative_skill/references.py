import re
from collections.abc import Iterator
from typing import Any

from backend.skills.base_schemas import ToolPlan

_STEP_REFERENCE = re.compile(r"^\$\{steps\.([^.}]+)\.data\}$")
_INPUT_REFERENCE = re.compile(r"^\$\{inputs\.([^}]+)\}$")


def parse_reference(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    for kind, pattern in (("steps", _STEP_REFERENCE), ("inputs", _INPUT_REFERENCE)):
        match = pattern.fullmatch(value)
        if match:
            return kind, match.group(1)
    if value.startswith("${"):
        raise ValueError(f"不支持的数据引用格式：{value}")
    return None


def iter_references(value: Any) -> Iterator[tuple[str, str]]:
    """递归遍历参数，使 Planner 与 Executor 使用同一引用协议。"""
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_references(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_references(item)
    else:
        reference = parse_reference(value)
        if reference:
            yield reference


def plan_dependencies(plan: ToolPlan) -> dict[str, set[str]]:
    identifiers = {step.step_id for step in plan.steps}
    output_keys = [step.output_key for step in plan.steps if step.output_key]
    if len(output_keys) != len(set(output_keys)):
        raise ValueError("计划中的 output_key 必须唯一。")
    for step in plan.steps:
        if step.output_key in identifiers and step.output_key != step.step_id:
            raise ValueError("output_key 不能覆盖其他步骤标识。")

    dependencies = {
        step.step_id: {
            identifier
            for kind, identifier in iter_references(step.arguments)
            if kind == "steps"
        }
        for step in plan.steps
    }
    for step_id, required in dependencies.items():
        unknown = required - identifiers
        if unknown:
            raise ValueError(f"步骤 {step_id} 引用了不存在的步骤：{sorted(unknown)}")

    # 拓扑检查允许前向引用，但拒绝循环依赖，避免并发执行时一直等待。
    pending = dict(dependencies)
    completed: set[str] = set()
    while pending:
        ready = {key for key, required in pending.items() if required <= completed}
        if not ready:
            raise ValueError("计划存在循环依赖。")
        completed.update(ready)
        pending = {
            key: required for key, required in pending.items() if key not in ready
        }
    return dependencies
