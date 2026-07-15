
from pydantic import BaseModel, Field


class FileResource(BaseModel):
    """
    统一的文件资源描述对象。

    所有"导出/生成文件"类工具（矢量、栅格、图表、报告等）都应将产出物
    封装为 FileResource 放入 ToolResult.data 中，方便下游 Agent/Skill
    以统一结构消费文件产出（路径、格式、大小及自定义元数据）。
    """
    path: str = Field(description="文件的相对路径")
    file_name: str = Field(description="文件名（含后缀）")
    format: str = Field(description="文件格式，如 shp、geojson、gpkg、png 等")
    size_bytes: int = Field(default=0, description="文件大小（字节）")
    metadata: dict = Field(default_factory=dict, description="与文件内容相关的附加元数据")