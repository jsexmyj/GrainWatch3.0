import tempfile
import os
from datetime import datetime
import atexit

from backend.utils.config import ConfigManager



# 暂时没用
# 全局变量，用于存储会话临时根目录
_SESSION_TEMP_ROOT = None
_TRACKED_TEMP_FILES = set()
_TRACKED_TEMP_DIRS = set()


def _get_session_temp_root():
    """获取或创建会话临时根目录"""
    global _SESSION_TEMP_ROOT
    if _SESSION_TEMP_ROOT is None:
        PROGRAM_START_TIME = datetime.now().strftime("%Y%m%d_%H%M%S")
        CONFIG_TEMP_DIR = ConfigManager.get("temp_dir") or tempfile.gettempdir()
        _SESSION_TEMP_ROOT = os.path.join(CONFIG_TEMP_DIR, f"{PROGRAM_START_TIME}")
        os.makedirs(_SESSION_TEMP_ROOT, exist_ok=True)
    return _SESSION_TEMP_ROOT


def mkd_temp(prefix="tmp", suffix="", dir=None):
    """
    创建临时文件。会在进程退出时自动删除。

    - 如果 `dir` 被指定：在该目录下创建临时文件
    - 如果 `dir` 为 None：在会话临时根目录下创建临时文件

    注意：在 Windows 上，打开的临时文件不能被其他进程删除，因此我们使用 delete=False 并在退出时删除。
    """
    base_dir = dir if dir else _get_session_temp_root()
    os.makedirs(base_dir, exist_ok=True)

    # 创建命名临时文件，便于直接用 open 写入
    tmp = tempfile.NamedTemporaryFile(
        prefix=prefix, suffix=suffix, dir=base_dir, delete=False
    )
    tmp_path = tmp.name
    try:
        tmp.close()
    except Exception:
        pass

    _TRACKED_TEMP_FILES.add(tmp_path)
    return tmp_path


def mkd_tempdir(prefix="tmp", suffix="", dir=None):
    """
    创建一个临时子目录（受进程退出时自动删除的追踪）。

    - 如果 `dir` 被指定：在该目录下创建一个唯一的临时子目录并返回路径。
    - 如果 `dir` 为 None：在会话临时根目录下创建并返回目录路径。
    """
    base_dir = dir if dir else _get_session_temp_root()
    os.makedirs(base_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix=prefix, suffix=suffix, dir=base_dir)
    _TRACKED_TEMP_DIRS.add(tmp_dir)
    return tmp_dir


def cleanup_all():
    """
    清理整个 SESSION_TEMP_ROOT 目录（慎用）
    """
    # 删除追踪的单个临时文件
    for p in list(_TRACKED_TEMP_FILES):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        finally:
            _TRACKED_TEMP_FILES.discard(p)

    # 删除追踪的临时目录
    import shutil

    for d in list(_TRACKED_TEMP_DIRS):
        try:
            if os.path.exists(d):
                shutil.rmtree(d)
        except Exception:
            pass
        finally:
            _TRACKED_TEMP_DIRS.discard(d)

    # 删除会话临时根目录下的其余内容（保守删除）
    session_root = _get_session_temp_root()
    if os.path.exists(session_root):
        try:
            shutil.rmtree(session_root)
        except Exception:
            # 若删除失败（文件被占用），忽略
            pass


# 在进程退出时尝试清理追踪的临时文件
def _atexit_cleanup():
    cleanup_all()


atexit.register(_atexit_cleanup)
