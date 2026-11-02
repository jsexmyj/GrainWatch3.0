# Validator 验证器

本模块为 GIS 工具与数据处理流程提供可自由组合的前置验证能力。验证器只判断输入是否合法并返回问题，不修改任何数据。

## 设计模式

### 职责链模式

每个 `BaseValidator` 子类只验证一件事，例如：

- 文件是否存在、可读
- 数据是否为空
- 字段是否存在、是否全为空、是否为数值类型
- 几何是否为空
- CRS 是否存在
- 几何类型与 CRS 是否适合直接进行长度、面积计算

验证器通过统一的 `validate(context)` 协议接收 `ValidationContext`，返回 `ValidationResult`。

### 组合模式

`ValidationPipeline` 同样继承 `BaseValidator`，因此流水线可以像普通验证器一样嵌套到另一条流水线中。文件、字段、几何等局部检查可以分别组装，再组合为完整的业务校验流程。

```text
ValidationPipeline
|- FileExistsValidator
|- FileReadableValidator
|- DataNotEmptyValidator
`- ValidationPipeline
   |- GeometryNotEmptyValidator
   `- CRSNotNullValidator
```

## 核心数据契约

`ValidationResult` 包含：

- `valid`：是否通过。
- `message`：中文验证信息。
- `validator_name`：产生结果的验证器名称。
- `details`：附加详情。流水线校验时可包含完整的 `results` 与失败项 `errors`。

`ValidationContext` 是所有验证器共享的输入上下文，可按需提供 `file_path`、`tool_name`、`tool_registry`、`gdf`、`field_names`、`allowed_geometry_types` 等数据。

## 三种调用方式

### 1. 调用 `validate`：只执行校验逻辑

适用场景：单元测试、内部逻辑判断，或不希望写入错误日志的场景。

```python
from backend.infrastructure.validator.common.field_validator import FieldExistsValidator

result = FieldExistsValidator().validate(context)

if not result.valid:
	print(result.message)
```

`validate` 仅返回 `ValidationResult`，失败时不记录日志。

### 2. 调用 `run`：执行单个验证器并记录日志

适用场景：业务代码直接执行一个验证器，希望失败原因同步写入日志。

```python
from backend.infrastructure.validator.geo.crs_validator import CRSNotNullValidator

result = CRSNotNullValidator().run(context)

if not result.valid:
	return result
```

`run` 内部调用 `validate`。验证失败时，统一通过 `logger.error` 输出中文日志。

### 3. 调用 `run_all`：获取流水线的逐项结果

适用场景：API 需要向调用方返回所有验证失败项，或调试、测试一组验证器的执行结果。

```python
from backend.infrastructure.validator.common.file_validator import (
	DataNotEmptyValidator,
	FileExistsValidator,
)
from backend.infrastructure.validator.geo.crs_validator import CRSNotNullValidator
from backend.infrastructure.validator.pipeline import ValidationPipeline

pipeline = ValidationPipeline(
	validators=[
		FileExistsValidator(),
		DataNotEmptyValidator(),
		CRSNotNullValidator(),
	]
)

pipeline_result = pipeline.run_all(context)

if not pipeline_result.valid:
	for error in pipeline_result.errors:
		print(f"{error.validator_name}: {error.message}")
```

`run_all` 返回 `PipelineResult`：

- `valid`：全部验证器是否通过。
- `results`：每个验证器的完整结果。
- `errors`：仅包含未通过的结果。

## 嵌套流水线示例

当一组验证器需要作为一个整体复用时，可将其组合为子流水线：

```python
from backend.infrastructure.validator.common.file_validator import (
	FileExistsValidator,
	FileReadableValidator,
)
from backend.infrastructure.validator.geo.crs_validator import CRSNotNullValidator
from backend.infrastructure.validator.geo.geometry_validator import (
	GeometryNotEmptyValidator,
)
from backend.infrastructure.validator.pipeline import ValidationPipeline

file_pipeline = ValidationPipeline(
	[FileExistsValidator(), FileReadableValidator()],
	validator_name="文件验证",
)

geo_pipeline = ValidationPipeline(
	[GeometryNotEmptyValidator(), CRSNotNullValidator()],
	validator_name="空间数据验证",
)

pipeline = ValidationPipeline([file_pipeline, geo_pipeline])
result = pipeline.run(context)

if not result.valid:
	print(result.message)
	print(result.details["errors"])
```

顶层使用 `run()` 时，流水线整体失败会记录日志；详细结果可从 `result.details["results"]` 和 `result.details["errors"]` 获取。
