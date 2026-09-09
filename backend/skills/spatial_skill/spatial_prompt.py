import json
from typing import Any

from backend.skills.spatial_skill.schemas import SpatialSkillTask


def build_spatial_planning_prompt(
    task: SpatialSkillTask,
    tool_catalog: dict[str, dict[str, Any]],
    output_mode: str = "tool_calling",
) -> str:
    """构建空间规划提示词，支持 JSON 与 tool calling 两种输出模式。"""

    payload = {
        "task": task.model_dump(mode="json"),
        "tools": tool_catalog,
        "output_contract": {
            "plan_id": "string",
            "steps": [
                {
                    "step_id": "string",
                    "tool_name": "registered tool name",
                    "arguments": "tool input object",
                    "output_key": "optional context key",
                    "on_failure": "raise or retry or warn",
                }
            ],
        },
        "reference_syntax": "Use ${steps.<step_id>.data} only for a previous step result.",
        "output_mode": output_mode,
    }
    return (
        "You are a spatial analysis planner. Select only tools listed in tools, create "
        "a sequential plan, and satisfy each selected tool input schema. Do not write "
        "Python or unregistered tool names. If output_mode=tool_calling, return only tool arguments; "
        "if output_mode=json, return only a JSON object matching output_contract.\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )
