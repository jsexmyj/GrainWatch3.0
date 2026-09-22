import os
from pathlib import Path
from typing import Iterable, Optional


def _detect_project_root() -> Path:
    """
    基于固定目录结构：
    GrainWatch3.0/backend/utils/paths.py
    """
    return Path(__file__).resolve().parents[2]

def resolve_file_path(file_path: str) -> Path:
    """
    基于 PATHS.root 进行文件路径解析
    """

    path = Path(file_path)
    if not path.is_absolute():
        path = (PATHS.root / path).resolve()
    else:
        path = path.resolve()

    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"输入路径不是文件: {path}")
    return path

class PathManager:
    """
    工程级路径管理器
    所有路径必须从这里获取，禁止业务代码自行拼路径
    """

    def __init__(self, project_root: Path):
        self._root = project_root

        # 一级目录
        self.backend = project_root / "backend"
        self.data = project_root / "data"
        self.logs = project_root / "LOGS"
        self.tests = project_root / "tests"

        # ================== 2. Backend 内部核心职责层 ==================
        # API 层
        self.api = self.backend / "api"
        
        # 智能体决策层 (Modules)
        self.modules = self.backend / "modules"
        self.master_agent = self.modules / "master_agent"
        self.spatial_agent = self.modules / "spatial_agent"
        self.knowledge_agent = self.modules / "knowledge_agent"

        # 能力封装层 (Skills)
        self.skills = self.backend / "skills"

        # 技术实现层 (Infrastructure)
        self.infrastructure = self.backend / "infrastructure"

        # 工具层 (Utils)
        self.utils = self.backend / "utils"

        # ================== 3. 资源层 (Resources) ==================
        self.resources = self.backend / "resources"
        # self.prompts = self.resources / "prompts"
        # self.templates = self.resources / "templates"
        # self.policies = self.resources / "policies"
        # self.cases = self.resources / "cases"
        # self.examples = self.resources / "examples"

    # ========== 基础能力 ==========
    @property
    def root(self) -> Path:
        return self._root

    def ensure_dir(self, path: Path) -> Path:
        """
        确保目录存在（常用于 output / logs）
        """
        path.mkdir(parents=True, exist_ok=True)
        return path

    def join(self, base: Path, *paths: str) -> Path:
        """
        安全 join，多级路径
        """
        return base.joinpath(*paths).resolve()

    # ========== backend 相关 ==========
    def api_path(self, *paths: str) -> Path:
        """api 目录下的路径"""
        return self.join(self.api, *paths)

    def skills_path(self, *paths: str) -> Path:
        """skills 目录下的路径"""
        return self.join(self.skills, *paths)

    def infrastructure_path(self, *paths: str) -> Path:
        """infrastructure 目录下的路径"""
        return self.join(self.infrastructure, *paths)

    def resources_path(self, *paths: str) -> Path:
        """resources 目录下的路径"""
        return self.join(self.resources, *paths)

    def utils_path(self, *paths: str) -> Path:
        """utils 目录下的路径"""
        return self.join(self.utils, *paths)

    # ========== Agent 相关（modules 子目录） ==========

    def master_agent_path(self, *paths: str) -> Path:
        """master_agent 目录下的路径"""
        return self.join(self.master_agent, *paths)

    def spatial_agent_path(self, *paths: str) -> Path:
        """spatial_agent 目录下的路径"""
        return self.join(self.spatial_agent, *paths)

    def knowledge_agent_path(self, *paths: str) -> Path:
        """knowledge_agent 目录下的路径"""
        return self.join(self.knowledge_agent, *paths)


    # ========== data 相关 ==========

    def data_path(self, *paths: str, mkdir: bool = True) -> Path:
        """data 目录下的路径，默认自动创建目录"""
        path = self.join(self.data, *paths)
        if mkdir:
            self.ensure_dir(path)
        return path


    # ========== logs 相关 ==========

    def log_path(self, *paths: str, mkdir: bool = True) -> Path:
        """logs 目录下的路径，默认自动创建父目录"""
        path = self.join(self.logs, *paths)
        if mkdir:
            self.ensure_dir(path.parent)
        return path

    # ========== tests 相关 ==========

    def tests_path(self, *paths: str, mkdir: bool = False) -> Path:
        path = self.join(self.tests, *paths)
        if mkdir:
            self.ensure_dir(path)
        return path

    # ========== YAML 路径解析（重点） ==========

    def resolve_from_backend(self, relative_path: str) -> Path:
        """
        解析基于 backend 目录的逻辑路径
        e.g. "modules/master_agent/config.yaml"
        """
        return self.join(self.backend, relative_path)

    # ========== 安全检查 ==========
    def assert_exists(self, path: Path, desc: Optional[str] = None):
        if not path.exists():
            raise FileNotFoundError(
                f"❌ 路径不存在: {path}" + (f" ({desc})" if desc else "")
            )
    
    def assert_is_file(self, path: Path, desc: Optional[str] = None):
        """断言路径必须是文件"""
        self.assert_exists(path, desc)
        if not path.is_file():
            raise IsADirectoryError(
                f"❌ 期望是文件但得到目录: {path}" + (f" ({desc})" if desc else "")
            )


# ========== 全局单例（工程统一入口） ==========
PATHS = PathManager(_detect_project_root())
