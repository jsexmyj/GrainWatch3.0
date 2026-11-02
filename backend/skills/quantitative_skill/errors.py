from typing import Any


class QuantitativeExecutionError(RuntimeError):
    """保留失败位置，供上游定位问题或安排后续恢复流程。"""

    def __init__(
        self,
        reason: str,
        *,
        step_id: str | None = None,
        tool_name: str | None = None,
        category: str = "fatal",
    ):
        self.reason = reason
        self.step_id = step_id
        self.tool_name = tool_name
        self.category = category
        super().__init__(
            f"量化执行失败 step_id={step_id}, tool={tool_name}, "
            f"category={category}, reason={reason}"
        )


class QuantitativeDataError(QuantitativeExecutionError):
    """数据需修复时暂时抛错，后续可由 Agent 路由到其他 Skill。"""

    def __init__(self, reason: str, **kwargs: Any):
        super().__init__(reason, category="data", **kwargs)
