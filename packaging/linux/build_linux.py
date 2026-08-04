#!/usr/bin/env python3
"""在 Ubuntu 22.04+ 上生成 SerialTool 便携包和 Debian 安装包。"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


APP_NAME = "SerialTool"
DEB_PACKAGE_NAME = "serialtool"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def normalize_architecture(machine: str) -> tuple[str, str]:
    """返回发布文件架构名和 Debian 架构名。"""
    normalized = machine.strip().lower()
    if normalized in {"x86_64", "amd64"}:
        return "x86_64", "amd64"
    if normalized in {"aarch64", "arm64"}:
        return "aarch64", "arm64"
    raise ValueError(f"暂不支持的 Linux 架构：{machine}")


def read_version(project_root: Path = PROJECT_ROOT) -> str:
    """从 theme.py 中读取版本号，避免构建阶段导入 GUI 依赖。"""
    content = (project_root / "theme.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if not match:
        raise RuntimeError("无法从 theme.py 读取 VERSION")
    return match.group(1)


def pyinstaller_arguments(
    project_root: Path,
    dist_dir: Path,
    work_dir: Path,
    spec_dir: Path,
) -> list[str]:
    """生成仅面向 Linux 的 PyInstaller 参数。"""
    project_root = Path(project_root)

    def linux_path(path: Path) -> str:
        return Path(path).as_posix()

    return [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--noupx",
        "--name",
        APP_NAME,
        "--distpath",
        linux_path(dist_dir),
        "--workpath",
        linux_path(work_dir),
        "--specpath",
        linux_path(spec_dir),
        "--add-data",
        f"{linux_path(project_root / '图标.png')}:.",
        "--hidden-import",
        "pyqtgraph",
        "--hidden-import",
        "numpy",
        "--hidden-import",
        "ota_center",
        "--hidden-import",
        "gsm_debugger",
        "--hidden-import",
        "auto_reply",
        "--hidden-import",
        "data_viewer",
        linux_path(project_root / "serial_GUI.py"),
    ]


def debian_control(version: str, deb_arch: str) -> str:
    """生成 Debian control 文件内容。"""
    return f"""Package: {DEB_PACKAGE_NAME}
Version: {version}
Section: utils
Priority: optional
Architecture: {deb_arch}
Maintainer: GAOXIANG <770807059@qq.com>
Depends: libc6 (>= 2.35), libgl1, libxkbcommon-x11-0, libxcb-xinerama0, libxcb-keysyms1, libxcb-shape0, libxcb-icccm4, libxcb-cursor0
Recommends: fonts-noto-cjk
Description: 串口、UDP 和 TCP 调试工具
 SerialTool 是基于 PyQt5 的多协议通信与数据分析桌面工具。
"""


def create_debian_layout(
    source_dir: Path,
    package_root: Path,
    version: str,
    deb_arch: str,
    icon_path: Path,
    license_path: Path,
) -> None:
    """把 PyInstaller onedir 产物组装成 Debian 包目录。"""
    source_dir = Path(source_dir)
    package_root = Path(package_root)
    install_dir = package_root / "opt" / APP_NAME
    shutil.copytree(source_dir, install_dir)
    # WSL 挂载盘会把所有文件显示为 0777。复制到原生临时目录后显式收紧，
    # 避免生成包含全局可写程序文件的 Debian 包。
    for current_dir, directory_names, file_names in os.walk(install_dir):
        Path(current_dir).chmod(0o755)
        for directory_name in directory_names:
            (Path(current_dir) / directory_name).chmod(0o755)
        for file_name in file_names:
            (Path(current_dir) / file_name).chmod(0o644)
    (install_dir / APP_NAME).chmod(0o755)

    control_path = package_root / "DEBIAN" / "control"
    control_path.parent.mkdir(parents=True)
    control_path.write_text(debian_control(version, deb_arch), encoding="utf-8")
    control_path.chmod(0o644)

    launcher = package_root / "usr" / "bin" / DEB_PACKAGE_NAME
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        '#!/bin/sh\nexec /opt/SerialTool/SerialTool "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    launcher.chmod(0o755)

    desktop_target = (
        package_root / "usr" / "share" / "applications" / "serialtool.desktop"
    )
    desktop_target.parent.mkdir(parents=True)
    shutil.copy2(Path(__file__).with_name("SerialTool.desktop"), desktop_target)
    desktop_target.chmod(0o644)

    icon_target = (
        package_root
        / "usr"
        / "share"
        / "icons"
        / "hicolor"
        / "256x256"
        / "apps"
        / "serialtool.png"
    )
    icon_target.parent.mkdir(parents=True)
    shutil.copy2(icon_path, icon_target)
    icon_target.chmod(0o644)

    copyright_target = (
        package_root / "usr" / "share" / "doc" / DEB_PACKAGE_NAME / "copyright"
    )
    copyright_target.parent.mkdir(parents=True)
    shutil.copy2(license_path, copyright_target)
    copyright_target.chmod(0o644)


def _reset_build_directory(path: Path, allowed_parent: Path) -> None:
    """仅清理已确认位于构建根目录下的目录。"""
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if resolved == parent or parent not in resolved.parents:
        raise RuntimeError(f"拒绝清理构建根目录之外的路径：{resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def build_python_bundle(project_root: Path, build_root: Path) -> Path:
    """调用当前 Linux Python 环境中的 PyInstaller。"""
    dist_dir = build_root / "pyinstaller-dist"
    work_dir = build_root / "pyinstaller-work"
    spec_dir = build_root / "spec"
    for directory in (dist_dir, work_dir, spec_dir):
        directory.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        *pyinstaller_arguments(project_root, dist_dir, work_dir, spec_dir),
    ]
    subprocess.run(command, cwd=project_root, check=True)
    bundle_dir = dist_dir / APP_NAME
    executable = bundle_dir / APP_NAME
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller 未生成预期入口：{executable}")
    executable.chmod(executable.stat().st_mode | 0o111)
    return bundle_dir


def build_portable_archive(
    bundle_dir: Path,
    output_dir: Path,
    version: str,
    release_arch: str,
) -> Path:
    """生成无需安装的 onedir 压缩包。"""
    release_name = f"{APP_NAME}-{version}-ubuntu22.04-{release_arch}"
    archive_path = output_dir / f"{release_name}.tar.gz"

    def normalize_tar_entry(member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.uid = 0
        member.gid = 0
        member.uname = "root"
        member.gname = "root"
        if member.isdir():
            member.mode = 0o755
        elif member.issym() or member.islnk():
            member.mode = 0o777
        elif member.name == f"{release_name}/{APP_NAME}":
            member.mode = 0o755
        else:
            member.mode = 0o644
        return member

    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(
            bundle_dir,
            arcname=release_name,
            recursive=True,
            filter=normalize_tar_entry,
        )
    return archive_path


def build_deb_package(
    bundle_dir: Path,
    output_dir: Path,
    version: str,
    release_arch: str,
    deb_arch: str,
    project_root: Path,
) -> Path:
    """使用 dpkg-deb 生成 Ubuntu 可安装包。"""
    output_path = output_dir / f"{APP_NAME}-{version}-ubuntu22.04-{release_arch}.deb"
    # WSL 的 Windows 挂载盘通常把目录权限固定为 0777，无法满足 dpkg-deb
    # 对 DEBIAN 目录的权限要求，因此始终在 Linux 原生临时目录中组装。
    with tempfile.TemporaryDirectory(prefix="serialtool-deb-") as temp_dir:
        package_root = Path(temp_dir) / "deb-root"
        create_debian_layout(
            source_dir=bundle_dir,
            package_root=package_root,
            version=version,
            deb_arch=deb_arch,
            icon_path=project_root / "图标.png",
            license_path=project_root / "LICENSE",
        )
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", package_root, output_path],
            check=True,
        )
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Ubuntu 22.04+ SerialTool 发布包")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "dist" / "linux",
        help="发布包输出目录",
    )
    parser.add_argument("--skip-deb", action="store_true", help="只生成便携压缩包")
    args = parser.parse_args()

    if not sys.platform.startswith("linux"):
        parser.error("Linux 发布包必须在 Ubuntu 22.04+ 环境中构建")

    release_arch, deb_arch = normalize_architecture(platform.machine())
    version = read_version(PROJECT_ROOT)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    build_parent = PROJECT_ROOT / "build"
    build_parent.mkdir(parents=True, exist_ok=True)
    build_root = build_parent / "linux"
    _reset_build_directory(build_root, build_parent)

    print(f"构建 SerialTool {version}，目标架构：{release_arch}")
    bundle_dir = build_python_bundle(PROJECT_ROOT, build_root)
    portable = build_portable_archive(
        bundle_dir, output_dir, version, release_arch
    )
    print(f"便携包：{portable}")

    if not args.skip_deb:
        if not shutil.which("dpkg-deb"):
            raise RuntimeError("缺少 dpkg-deb，请先安装 dpkg-dev")
        deb_path = build_deb_package(
            bundle_dir,
            output_dir,
            version,
            release_arch,
            deb_arch,
            PROJECT_ROOT,
        )
        print(f"Debian 安装包：{deb_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
