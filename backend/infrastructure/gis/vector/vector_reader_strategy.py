import os
from abc import ABC, abstractmethod
import geopandas as gpd
import rasterio
from typing import Tuple, Dict, Type

# ==========================================
# 矢量读取策略定义
# ==========================================

class VectorReaderStrategy(ABC):
    """矢量数据读取抽象策略"""
    @abstractmethod
    def read(self, source: str, **kwargs) -> gpd.GeoDataFrame:
        pass


class ShapefileReader(VectorReaderStrategy):
    """Shapefile 读取策略"""
    def read(self, source: str, **kwargs) -> gpd.GeoDataFrame:
        # 默认支持中文字符编码处理
        encoding = kwargs.get("encoding", "utf-8")
        return gpd.read_file(source, encoding=encoding, **kwargs)


class GeoJSONReader(VectorReaderStrategy):
    """GeoJSON 读取策略"""
    def read(self, source: str, **kwargs) -> gpd.GeoDataFrame:
        return gpd.read_file(source, driver="GeoJSON", **kwargs)


class PostGISReader(VectorReaderStrategy):
    """PostGIS 数据库读取策略"""
    def read(self, source: str, **kwargs) -> gpd.GeoDataFrame:
        sql = kwargs.get("sql")
        if not sql:
            raise ValueError("从 PostGIS 读取数据时，必须提供 'sql' 查询语句或表名。")
        
        # 使用 SQLAlchemy 创建连接
        from sqlalchemy import create_engine
        engine = create_engine(source)
        # 支持传入 geom_col 等参数
        geom_col = kwargs.get("geom_col", "geom")
        cleaned_kwargs = {key: value for key, value in kwargs.items() if key not in {"sql", "geom_col"}}
        return gpd.read_postgis(sql, con=engine, geom_col=geom_col, **cleaned_kwargs)


# 矢量策略注册中心
VECTOR_STRATEGIES: Dict[str, Type[VectorReaderStrategy]] = {
    "shp": ShapefileReader,
    "geojson": GeoJSONReader,
    "postgres": PostGISReader,
    "postgresql": PostGISReader
}

