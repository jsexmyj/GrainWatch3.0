from datetime import datetime
import logging
import os
from pathlib import Path
from backend.utils.paths import PATHS


global_log_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def get_logger(
    name=__name__, log_filename=f"{global_log_time}.log", level: int = logging.DEBUG
):
    logger = logging.getLogger(name)

    if not logger.handlers:  # 防止重复添加 handler
        logger.setLevel(level)

        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 控制台输出
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 创建文件处理器
        log_file: Path = PATHS.log_path(log_filename)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # 禁用传播到根日志记录器
        logger.propagate = False

    return logger
