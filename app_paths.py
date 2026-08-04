"""应用资源与用户可写目录解析。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Mapping, NamedTuple


APP_NAME = "SerialTool"


class AppPaths(NamedTuple):
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    logs_dir: Path
    ota_dir: Path


def resolve_app_paths(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> AppPaths:
    """根据平台返回配置、数据和缓存目录。"""
    platform_name = platform_name or sys.platform
    env = os.environ if environ is None else environ
    working_dir = Path.cwd() if cwd is None else Path(cwd)

    if platform_name.startswith("linux"):
        home = Path(env.get("HOME", str(Path.home())))
        config_dir = Path(env.get("XDG_CONFIG_HOME", str(home / ".config"))) / APP_NAME
        data_dir = Path(env.get("XDG_DATA_HOME", str(home / ".local" / "share"))) / APP_NAME
        cache_dir = Path(env.get("XDG_CACHE_HOME", str(home / ".cache"))) / APP_NAME
    else:
        config_dir = working_dir
        data_dir = working_dir
        cache_dir = working_dir

    return AppPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        logs_dir=data_dir / "logs",
        ota_dir=data_dir / "ota_serve",
    )


def ensure_user_dirs(paths: AppPaths) -> None:
    """创建运行时需要的用户可写目录。"""
    for path in (
        paths.config_dir,
        paths.data_dir,
        paths.cache_dir,
        paths.logs_dir,
        paths.ota_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def resource_path(relative_path: str, bundle_dir: Path | None = None) -> Path:
    """解析源码运行和 PyInstaller 打包后的只读资源路径。"""
    if bundle_dir is None:
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return Path(bundle_dir) / relative_path


def open_directory(
    path: Path | str,
    opener: Callable[[Path], bool] | None = None,
) -> bool:
    """使用桌面环境的默认文件管理器打开目录。"""
    directory = Path(path).resolve()
    if opener is not None:
        return bool(opener(directory))

    from PyQt5.QtCore import QUrl
    from PyQt5.QtGui import QDesktopServices

    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(directory))))
