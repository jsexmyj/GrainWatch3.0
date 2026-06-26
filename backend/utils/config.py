from copy import deepcopy
import yaml
from typing import Any, Optional
from pathlib import Path
from backend.utils.paths import PATHS


class ConfigManager:
    _configs: dict[str, dict] = {}

    @classmethod
    def load_config(cls, name: str = "default", path: Path = PATHS.utils_path("config.yaml")):
        if name in cls._configs:
            return  # 已加载就不重复读

        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as f:
            cls._configs[name] = yaml.safe_load(f)

        if path is None:
            # 默认情况：找 config.py 同级的 config.yaml
            current_dir = Path(__file__).resolve().parent
            config_path = current_dir / "config.yaml"
        

        # 调试打印 (排错用，确认路径是否正确)
        # print(f"尝试加载配置 [{name}]: {config_path}")

        if not config_path.exists():
            # 打印出完整的绝对路径，方便排查
            raise FileNotFoundError(f"配置文件未找到: {config_path}")

        with config_path.open("r", encoding="utf-8") as f:
            cls._configs[name] = yaml.safe_load(f)

    # get 方法保持不变...
    @classmethod
    def get(
        cls,
        key: str,
        default: Any = None,
        config_name: str = "default",
    ) -> Any:
        if config_name not in cls._configs:
            raise ValueError(f"Config '{config_name}' not loaded")

        # 防止配置被业务代码意外修改
        value = deepcopy(cls._configs[config_name])
        keys = key.split(".")
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value