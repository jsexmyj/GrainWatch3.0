import json
from typing import Any

from backend.agent.base_schemas import BaseSkillTask


def build_quantitative_planning_prompt(
    task: BaseSkillTask,
    tool_catalog: dict[str, dict[str, Any]],
    output_mode: str = "tool_calling",
) -> str:
    payload = {
        "task": task.model_dump(mode="json"),
        "tools": tool_catalog,
        "output_mode": output_mode,
        "output_contract": {
            "plan_id": "string",
            "steps": [
                {
                    "step_id": "unique string",
                    "tool_name": "registered statistics tool",
                    "arguments": "tool input object",
                    "output_key": "optional unique context key",
                    "on_failure": "raise or warn",
                }
            ],
        },
    }
    return (
        "You are a quantitative GIS planner for tasks from the spatial agent. "
        "Use only the listed statistics tools and satisfy their input schemas. "
        "Independent steps run concurrently; express actual data dependencies with "
        "${steps.<step_id>.data}. Never impose an artificial sequential chain. "
        "Use ${inputs.<data_id>} or a supplied file path as gdf/source. The executor "
        "loads vector files into GeoDataFrames automatically; raster tools receive paths. "
        "Do not plan vector_load, reprojection, repair, or other skills. "
        "Area/length require an appropriate projected CRS; do not invent CRS or fields. "
        "For grouped area totals, first use vector_area, then reference its data in "
        "group_aggregate with the matching area_field_name as metric. "
        "Default on_failure to raise. Use warn only for explicitly recoverable failures; "
        "invalid data always raises. Return only the plan object or tool-call arguments.\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )
