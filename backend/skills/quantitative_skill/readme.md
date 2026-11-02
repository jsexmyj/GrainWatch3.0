Quantitative Skill 接收 Spatial Agent 的 `QuantitativeSkillTask`，依次执行任务校验、
输入文件验证、LLM 规划、工具数据准备与验证、统计执行和 `build_evidence`。

`QuantitativeSkillPlanner` 复用 `PlanLLMStrategy`，只向模型提供
`backend/infrastructure/gis/statistics` 中已注册且满足 `allowed_tools` 的工具。
模型可以用 `${inputs.<data_id>}` 指向 `input_refs`，也可以直接使用文件路径。
真实数据依赖用 `${steps.<step_id>.data}` 表达；无依赖步骤并行，有依赖步骤等待输出。
支持前向引用，拒绝未知引用、循环依赖及重复的上下文键。

`excutor.py` 中的 `QuantitativeExecutor.prepare` 使用 `vector_loader.py` 加载 SHP、
GeoJSON/JSON，每次任务缓存同一来源；栅格保持文件路径。
`QuantitativeValidator` 组合文件、非空数据、字段、几何、CRS 和度量验证链，
在执行统计之前校验所有直接输入，依赖输出在消费时校验。
各步骤使用自己的 GDF 属性表副本，避免并发工具相互修改数据。
现有 GIS 工具内部为同步计算，默认在线程中运行，`max_concurrency` 默认 4。
若注入的工具调度器依赖当前事件循环，可设置 `offload_tools=False`。

失败处理规则：

- 文件损坏、空数据、缺失必要字段、无效几何和不满足度量要求的 CRS 抛错。
  `QuantitativeDataError` 保留步骤与工具标识，暂不自动调用其他 Skill。
- 部分属性缺失、空几何或 NoData 在仍有可用数据时继续，并返回 `warnings`。
  几何统计跳过空几何；属性和栅格工具遵循自身的空值选项。
- 工具返回失败默认抛错；只有 `metadata.failure_category="recoverable"`
  且步骤声明 `on_failure="warn"` 或 `"continue"` 才继续。
  声明 `failure_category="data"` 的失败始终抛错，依赖失败结果的步骤跳过并告警。
- 当前不支持 `retry`，防止将不可恢复的数据错误当作网络错误重试。

Evidence 包含成功工具的事实与统计元数据、文件引用、告警和执行状态，
不包含 GDF 或完整运行时上下文。有成功和失败结果时状态为 `partial`。

测试使用可控 LLM 替身验证三种难度的任务规划，真实临时 SHP/GeoJSON/GeoTIFF
验证加载、统计、异常与端到端串联，无需模型凭据或网络请求：

```powershell
python -m pytest tests/test_quantitative_skill_workflow.py -q -p no:cacheprovider
```
