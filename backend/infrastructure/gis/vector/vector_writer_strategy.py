from abc import ABC, abstractmethod
from typing import Dict, Type

import geopandas as gpd

# ==========================================
# 矢量写出策略定义
# ==========================================


class VectorWriterStrategy(ABC):
    """矢量数据写出抽象策略"""

    @abstractmethod
    def write(self, gdf: gpd.GeoDataFrame, destination: str, **kwargs) -> str:
        """
        将 GeoDataFrame 写出到指定目标路径。

        Returns:
            实际写出的文件路径（部分驱动可能会规范化路径/后缀）。
        """
        pass


class ShapefileWriter(VectorWriterStrategy):
    """Shapefile 写出策略"""

    def write(self, gdf: gpd.GeoDataFrame, destination: str, **kwargs) -> str:
        # 默认使用 utf-8 编码，避免中文属性字段写出后乱码
        encoding = kwargs.pop("encoding", "utf-8")
        gdf.to_file(destination, driver="ESRI Shapefile", encoding=encoding, **kwargs)
        return destination


class GeoJSONWriter(VectorWriterStrategy):
    """GeoJSON 写出策略"""

    def write(self, gdf: gpd.GeoDataFrame, destination: str, **kwargs) -> str:
        gdf.to_file(destination, driver="GeoJSON", **kwargs)
        return destination


class GeoPackageWriter(VectorWriterStrategy):
    """GeoPackage 写出策略（预留扩展，便于后续按需支持多图层输出）"""

    def write(self, gdf: gpd.GeoDataFrame, destination: str, **kwargs) -> str:
        layer = kwargs.pop("layer", None)
        gdf.to_file(destination, driver="GPKG", layer=layer, **kwargs)
        return destination


# 矢量写出策略注册中心：后续新增导出格式时，只需在此注册即可被工具自动识别
VECTOR_WRITER_STRATEGIES: Dict[str, Type[VectorWriterStrategy]] = {
    "shp": ShapefileWriter,
    "geojson": GeoJSONWriter,
    "gpkg": GeoPackageWriter,
}
