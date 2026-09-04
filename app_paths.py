"""应用资源与用户可写目录解析。"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Mapping, MutableMapping, NamedTuple


APP_NAME = "SerialTool"


class AppPaths(NamedTuple):
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    logs_dir: Path
    ota_dir: Path


def sanitize_linux_runtime_environment(
    platform_name: str | None = None,
    environ: MutableMapping[str, str] | None = None,
    current_uid: int | None = None,
    stat_result=None,
) -> bool:
    """清除不属于当前用户的 Qt 运行目录，避免 sudo 启动时继承错误环境。"""
    platform_name = platform_name or sys.platform
    if not platform_name.startswith("linux"):
        return False

    env = os.environ if environ is None else environ
    runtime_dir = env.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return False

    uid = os.getuid() if current_uid is None else current_uid
    try:
        metadata = stat_result if stat_result is not None else Path(runtime_dir).stat()
        valid = metadata.st_uid == uid and stat.S_IMODE(metadata.st_mode) == 0o700
    except OSError:
        valid = False

    if valid:
        return False
    env.pop("XDG_RUNTIME_DIR", None)
    return True


def _absolute_env_path(
    env: Mapping[str, str],
    key: str,
    default: Path,
    platform_name: str,
) -> Path:
    value = env.get(key, "")
    if not value:
        return Path(default)
    pure_path = PureWindowsPath(value) if platform_name == "win32" else PurePosixPath(value)
    return Path(value) if pure_path.is_absolute() else Path(default)


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
        home = _absolute_env_path(env, "HOME", Path.home(), platform_name)
        config_dir = _absolute_env_path(
            env, "XDG_CONFIG_HOME", home / ".config", platform_name
        ) / APP_NAME
        data_dir = _absolute_env_path(
            env, "XDG_DATA_HOME", home / ".local" / "share", platform_name
        ) / APP_NAME
        cache_dir = _absolute_env_path(
            env, "XDG_CACHE_HOME", home / ".cache", platform_name
        ) / APP_NAME
    elif platform_name == "win32":
        home = _absolute_env_path(env, "USERPROFILE", Path.home(), platform_name)
        roaming_dir = _absolute_env_path(
            env, "APPDATA", home / "AppData" / "Roaming", platform_name
        )
        local_dir = _absolute_env_path(
            env, "LOCALAPPDATA", home / "AppData" / "Local", platform_name
        )
        config_dir = roaming_dir / APP_NAME
        data_dir = local_dir / APP_NAME
        cache_dir = data_dir / "cache"
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


def migrate_legacy_user_data(
    paths: AppPaths,
    legacy_dir: Path | str,
) -> int:
    """把旧工作目录中的用户数据复制到新目录，已有目标文件保持不变。"""
    legacy_root = Path(legacy_dir).resolve()
    migrated = 0
    file_targets = {
        "serial_config.json": paths.config_dir / "serial_config.json",
        "data_viewer.ini": paths.config_dir / "data_viewer.ini",
    }
    for source_name, target in file_targets.items():
        source = legacy_root / source_name
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            migrated += 1
    return migrated


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
