from pathlib import Path
from typing import Any

import geopandas as gpd
import rasterio

from backend.infrastructure.validator.base import ValidationContext
from backend.infrastructure.validator.common.field_validator import (
    FieldExistsValidator,
    FieldNotAllNullValidator,
    FieldNumericValidator,
)
from backend.infrastructure.validator.common.file_validator import (
    DataNotEmptyValidator,
    FileExistsValidator,
    FileReadableValidator,
    ToolExistsValidator,
)
from backend.infrastructure.validator.geo.crs_validator import CRSNotNullValidator
from backend.infrastructure.validator.geo.geometry_validator import (
    GeometryNotEmptyValidator,
)
from backend.infrastructure.validator.pipeline import ValidationPipeline
from backend.infrastructure.validator.statistics.measurement_validator import (
    MeasurementValidator,
)
from backend.skills.quantitative_skill.errors import QuantitativeDataError
from backend.utils.paths import PATHS


class QuantitativeValidator:
    """按工具职责选择验证链；只在可统计的缺失记录上产生告警。"""

    def __init__(self):
        self.file_pipeline = ValidationPipeline(
            [FileExistsValidator(), FileReadableValidator()],
            stop_on_first_failure=True,
            validator_name="量化输入文件验证",
        )

    @staticmethod
    def _require(pipeline: ValidationPipeline, context: ValidationContext) -> None:
        result = pipeline.run_all(context)
        if not result.valid:
            raise QuantitativeDataError(
                "；".join(error.message for error in result.errors)
            )

    def validate_file(self, value: str | Path) -> str:
        path = PATHS.join(PATHS.root, str(value))
        self._require(self.file_pipeline, ValidationContext(file_path=path))
        return str(path)

    @staticmethod
    def validate_tool(tool_name: str, registry: Any) -> None:
        result = ToolExistsValidator().run(
            ValidationContext(tool_name=tool_name, tool_registry=registry)
        )
        if not result.valid:
            raise ValueError(result.message)

    def validate_arguments(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        arguments = dict(arguments)
        warnings: list[str] = []
        data = arguments.get("gdf", arguments.get("source"))
        if isinstance(data, gpd.GeoDataFrame):
            data, warnings = self._validate_vector(tool_name, data, arguments)
            key = "gdf" if "gdf" in arguments else "source"
            arguments[key] = data
        elif isinstance(data, (str, Path)):
            warnings = self._validate_raster(tool_name, str(data), arguments)
        return arguments, warnings

    def _validate_vector(
        self, tool_name: str, gdf: gpd.GeoDataFrame, arguments: dict[str, Any]
    ) -> tuple[gpd.GeoDataFrame, list[str]]:
        warnings: list[str] = []
        context = ValidationContext(gdf=gdf)
        self._require(ValidationPipeline([DataNotEmptyValidator()]), context)

        fields = [
            arguments[name]
            for name in ("field", "group_by", "metric")
            if arguments.get(name)
        ]
        if fields:
            context.field_names = list(dict.fromkeys(fields))
            self._require(ValidationPipeline([FieldExistsValidator()]), context)
            for field in context.field_names:
                missing = int(gdf[field].isna().sum())
                # count 可统计零个有效值；dropna=False 可将空值作为一个类别。
                allows_all_null = (
                    tool_name == "value_counts" and not arguments.get("dropna", True)
                ) or (
                    tool_name == "group_aggregate"
                    and arguments.get("aggregate") == "count"
                    and field == arguments.get("metric")
                    and field != arguments.get("group_by")
                )
                if not allows_all_null:
                    self._require(
                        ValidationPipeline([FieldNotAllNullValidator()]),
                        ValidationContext(gdf=gdf, field_names=[field]),
                    )
                if missing:
                    warnings.append(
                        f"字段 {field} 有 {missing} 条缺失值，统计将按工具的空值规则处理。"
                    )
            if tool_name == "group_aggregate" and arguments.get("aggregate") != "count":
                metric = arguments.get("metric")
                if not metric:
                    raise ValueError("非 count 聚合必须提供 metric。")
                self._require(
                    ValidationPipeline([FieldNumericValidator()]),
                    ValidationContext(gdf=gdf, field_names=[metric]),
                )
        elif tool_name == "group_aggregate":
            raise ValueError("分组聚合必须提供 group_by。")

        if tool_name in {"vector_area", "vector_length", "bbox"}:
            try:
                geometry = gdf.geometry
            except AttributeError as exc:
                raise QuantitativeDataError("数据缺少有效几何列。") from exc
            missing = geometry.isna() | geometry.is_empty
            if missing.any():
                # 只为本次度量创建有效数据副本，保留原始输入供其他独立统计使用。
                warnings.append(f"跳过 {int(missing.sum())} 条缺失或空几何记录。")
                gdf = gdf.loc[~missing].copy()
            if not gdf.geometry.is_valid.all():
                raise QuantitativeDataError("存在无效几何，需先由其他 Skill 修复。")
            validation_gdf = (
                gdf
                if gdf.geometry.name == "geometry"
                else gdf.rename_geometry("geometry")
            )
            geo_context = ValidationContext(gdf=validation_gdf)
            pipeline = ValidationPipeline(
                [DataNotEmptyValidator(), GeometryNotEmptyValidator()],
                stop_on_first_failure=True,
            )
            if tool_name in {"vector_area", "vector_length"}:
                geo_context.allowed_geometry_types = (
                    ["Polygon", "MultiPolygon"]
                    if tool_name == "vector_area"
                    else [
                        "LineString",
                        "MultiLineString",
                        "Polygon",
                        "MultiPolygon",
                        "LinearRing",
                    ]
                )
                pipeline.add(CRSNotNullValidator()).add(MeasurementValidator())
            self._require(pipeline, geo_context)
        return gdf, warnings

    def _validate_raster(
        self, tool_name: str, source: str, arguments: dict[str, Any]
    ) -> list[str]:
        warnings: list[str] = []
        try:
            with rasterio.open(source) as dataset:
                if dataset.width <= 0 or dataset.height <= 0 or dataset.count <= 0:
                    raise QuantitativeDataError("栅格不包含有效像元或波段。")
                band = arguments.get("band")
                if band is not None and (
                    not isinstance(band, int)
                    or isinstance(band, bool)
                    or not 1 <= band <= dataset.count
                ):
                    raise ValueError(f"波段 {band} 超出有效范围 1..{dataset.count}。")
                if tool_name == "raster_class_area":
                    if dataset.crs is None or not dataset.crs.is_projected:
                        raise QuantitativeDataError(
                            "栅格面积统计需要投影 CRS，需先由其他 Skill 处理。"
                        )
                    transform = dataset.transform
                    if (
                        transform.b != 0
                        or transform.d != 0
                        or transform.a == 0
                        or transform.e == 0
                    ):
                        raise QuantitativeDataError(
                            "当前面积工具仅支持非旋转且分辨率非零的栅格。"
                        )
                needs_values = tool_name in {
                    "raster_value_counts",
                    "raster_class_area",
                } or (
                    tool_name == "describe_raster"
                    and arguments.get("compute_stats", False)
                )
                if needs_values:
                    bands = (
                        [band]
                        if band is not None
                        else (
                            list(range(1, dataset.count + 1))
                            if tool_name == "describe_raster"
                            else [1]
                        )
                    )
                    for index in bands:
                        missing = 0
                        for _, window in dataset.block_windows(index):
                            missing += int(
                                (dataset.read_masks(index, window=window) == 0).sum()
                            )
                        total = dataset.width * dataset.height
                        if missing == total and not arguments.get(
                            "include_nodata", False
                        ):
                            raise QuantitativeDataError(
                                f"波段 {index} 全为 NoData，无法产生有效统计。"
                            )
                        if missing:
                            warnings.append(
                                f"波段 {index} 有 {missing} 个 NoData 像元，统计将按 include_nodata 规则处理。"
                            )
        except (QuantitativeDataError, ValueError):
            raise
        except Exception as exc:
            raise QuantitativeDataError(f"栅格无法读取或已损坏：{exc}") from exc
        return warnings
