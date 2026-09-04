#!/usr/bin/env python3
"""在 Ubuntu 22.04+ 上生成 SerialTool 便携包和 Debian 安装包。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import platform
import re
import shutil
import struct
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
        "--add-data",
        f"{linux_path(project_root / '图标.ico')}:.",
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


def debian_control(
    version: str,
    deb_arch: str,
    glibc_baseline: str = "2.35",
) -> str:
    """生成 Debian control 文件内容。"""
    return f"""Package: {DEB_PACKAGE_NAME}
Version: {version}
Section: utils
Priority: optional
Architecture: {deb_arch}
Maintainer: GAOXIANG <770807059@qq.com>
Depends: libc6 (>= {glibc_baseline}), libgl1, libxkbcommon-x11-0, libxcb-xinerama0, libxcb-keysyms1, libxcb-shape0, libxcb-icccm4, libxcb-cursor0
Recommends: fonts-noto-cjk
Description: 串口、UDP 和 TCP 调试工具
 SerialTool 是基于 PyQt5 的多协议通信与数据分析桌面工具。
"""


def _largest_png_from_ico(icon_path: Path) -> bytes:
    """从 ICO 中提取面积最大的内置 PNG，供 Linux 图标主题使用。"""
    content = Path(icon_path).read_bytes()
    if len(content) < 6:
        raise ValueError("ICO 文件头不完整")
    reserved, icon_type, count = struct.unpack_from("<HHH", content)
    if reserved != 0 or icon_type != 1 or count < 1:
        raise ValueError("不是有效的 ICO 文件")

    candidates = []
    for index in range(count):
        entry_offset = 6 + index * 16
        if entry_offset + 16 > len(content):
            raise ValueError("ICO 目录不完整")
        width, height, _, _, _, _, size, offset = struct.unpack_from(
            "<BBBBHHII", content, entry_offset
        )
        width = width or 256
        height = height or 256
        if offset + size <= len(content):
            image = content[offset:offset + size]
            if image.startswith(b"\x89PNG\r\n\x1a\n"):
                candidates.append((width * height, image))
    if not candidates:
        raise ValueError("ICO 中不包含 PNG 图像")
    return max(candidates, key=lambda item: item[0])[1]


def create_debian_layout(
    source_dir: Path,
    package_root: Path,
    version: str,
    deb_arch: str,
    icon_path: Path,
    license_path: Path,
    source_date_epoch: int = 0,
    glibc_baseline: str = "2.35",
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
    control_path.write_text(
        debian_control(version, deb_arch, glibc_baseline),
        encoding="utf-8",
    )
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
    if icon_path.suffix.lower() == ".ico":
        icon_target.write_bytes(_largest_png_from_ico(icon_path))
    else:
        shutil.copy2(icon_path, icon_target)
    icon_target.chmod(0o644)

    copyright_target = (
        package_root / "usr" / "share" / "doc" / DEB_PACKAGE_NAME / "copyright"
    )
    copyright_target.parent.mkdir(parents=True)
    shutil.copy2(license_path, copyright_target)
    copyright_target.chmod(0o644)

    def set_mtime(path: Path) -> None:
        try:
            os.utime(
                path,
                (source_date_epoch, source_date_epoch),
                follow_symlinks=False,
            )
        except NotImplementedError:
            os.utime(path, (source_date_epoch, source_date_epoch))

    for current_dir, directory_names, file_names in os.walk(package_root):
        for file_name in file_names:
            set_mtime(Path(current_dir) / file_name)
        for directory_name in directory_names:
            set_mtime(Path(current_dir) / directory_name)
    set_mtime(package_root)


def _reset_build_directory(path: Path, allowed_parent: Path) -> None:
    """仅清理已确认位于构建根目录下的目录。"""
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if resolved == parent or parent not in resolved.parents:
        raise RuntimeError(f"拒绝清理构建根目录之外的路径：{resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def validate_output_directory(output_dir: Path, build_root: Path) -> Path:
    """拒绝把发布目录放入即将清理的构建目录。"""
    resolved_output = Path(output_dir).resolve()
    resolved_build = Path(build_root).resolve()
    if resolved_output == resolved_build or resolved_build in resolved_output.parents:
        raise ValueError(f"输出目录不能位于待清理构建目录中：{resolved_output}")
    return resolved_output


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
    source_date_epoch: int = 0,
    ubuntu_baseline: str = "22.04",
) -> Path:
    """生成无需安装的 onedir 压缩包。"""
    release_name = f"{APP_NAME}-{version}-ubuntu{ubuntu_baseline}-{release_arch}"
    archive_path = output_dir / f"{release_name}.tar.gz"

    def normalize_tar_entry(member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.uid = 0
        member.gid = 0
        member.uname = "root"
        member.gname = "root"
        member.mtime = source_date_epoch
        if member.isdir():
            member.mode = 0o755
        elif member.issym() or member.islnk():
            member.mode = 0o777
        elif member.name == f"{release_name}/{APP_NAME}":
            member.mode = 0o755
        else:
            member.mode = 0o644
        return member

    with archive_path.open("wb") as archive_file:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=archive_file,
            mtime=source_date_epoch,
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                archive.add(
                    bundle_dir,
                    arcname=release_name,
                    recursive=True,
                    filter=normalize_tar_entry,
                )
    return archive_path


def source_date_epoch(project_root: Path = PROJECT_ROOT) -> int:
    """优先使用 SOURCE_DATE_EPOCH，否则使用当前 Git 提交时间。"""
    configured = os.environ.get("SOURCE_DATE_EPOCH")
    if configured is not None:
        try:
            return max(0, int(configured))
        except ValueError as exc:
            raise ValueError("SOURCE_DATE_EPOCH 必须是非负整数") from exc
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return max(0, int(result.stdout.strip()))
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def build_deb_package(
    bundle_dir: Path,
    output_dir: Path,
    version: str,
    release_arch: str,
    deb_arch: str,
    project_root: Path,
    source_date_epoch: int = 0,
    ubuntu_baseline: str = "22.04",
    glibc_baseline: str = "2.35",
) -> Path:
    """使用 dpkg-deb 生成 Ubuntu 可安装包。"""
    output_path = (
        output_dir
        / f"{APP_NAME}-{version}-ubuntu{ubuntu_baseline}-{release_arch}.deb"
    )
    # WSL 的 Windows 挂载盘通常把目录权限固定为 0777，无法满足 dpkg-deb
    # 对 DEBIAN 目录的权限要求，因此始终在 Linux 原生临时目录中组装。
    with tempfile.TemporaryDirectory(prefix="serialtool-deb-") as temp_dir:
        package_root = Path(temp_dir) / "deb-root"
        create_debian_layout(
            source_dir=bundle_dir,
            package_root=package_root,
            version=version,
            deb_arch=deb_arch,
            icon_path=project_root / "图标.ico",
            license_path=project_root / "LICENSE",
            source_date_epoch=source_date_epoch,
            glibc_baseline=glibc_baseline,
        )
        environment = {
            **os.environ,
            "SOURCE_DATE_EPOCH": str(source_date_epoch),
        }
        print("正在生成 Debian 安装包（gzip 压缩）……", flush=True)
        subprocess.run(
            [
                "dpkg-deb", "--root-owner-group", "-Zgzip", "-z6",
                "--build", package_root, output_path,
            ],
            check=True,
            env=environment,
        )
    return output_path


def write_checksums(artifacts: list[Path], output_dir: Path) -> Path:
    """为发布产物生成稳定排序的 SHA-256 校验文件。"""
    checksum_path = Path(output_dir) / "SHA256SUMS"
    lines = []
    for artifact in sorted((Path(path) for path in artifacts), key=lambda path: path.name):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}\n")
    checksum_path.write_text("".join(lines), encoding="utf-8", newline="\n")
    return checksum_path


def main(
    ubuntu_baseline: str = "22.04",
    glibc_baseline: str = "2.35",
    build_dir_name: str = "linux",
    default_output_dir: Path | None = None,
    required_ubuntu: str | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=f"构建 Ubuntu {ubuntu_baseline}+ SerialTool 发布包"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_output_dir or PROJECT_ROOT / "dist" / "linux",
        help="发布包输出目录",
    )
    parser.add_argument("--skip-deb", action="store_true", help="只生成便携压缩包")
    args = parser.parse_args()

    if not sys.platform.startswith("linux"):
        parser.error(f"Linux 发布包必须在 Ubuntu {ubuntu_baseline}+ 环境中构建")
    if required_ubuntu is not None:
        try:
            release = platform.freedesktop_os_release()
        except OSError as error:
            parser.error(f"无法识别 Ubuntu 版本：{error}")
        if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != required_ubuntu:
            parser.error(
                f"兼容 Ubuntu {ubuntu_baseline}+ 的正式发布包必须在 "
                f"Ubuntu {required_ubuntu} 中构建"
            )

    release_arch, deb_arch = normalize_architecture(platform.machine())
    version = read_version(PROJECT_ROOT)
    build_parent = PROJECT_ROOT / "build"
    build_parent.mkdir(parents=True, exist_ok=True)
    build_root = build_parent / build_dir_name
    output_dir = validate_output_directory(args.output_dir, build_root)
    _reset_build_directory(build_root, build_parent)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"构建 SerialTool {version}，目标架构：{release_arch}")
    bundle_dir = build_python_bundle(PROJECT_ROOT, build_root)
    build_epoch = source_date_epoch(PROJECT_ROOT)
    portable = build_portable_archive(
        bundle_dir,
        output_dir,
        version,
        release_arch,
        source_date_epoch=build_epoch,
        ubuntu_baseline=ubuntu_baseline,
    )
    print(f"便携包：{portable}")
    artifacts = [portable]

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
            source_date_epoch=build_epoch,
            ubuntu_baseline=ubuntu_baseline,
            glibc_baseline=glibc_baseline,
        )
        print(f"Debian 安装包：{deb_path}")
        artifacts.append(deb_path)
    checksum_file = write_checksums(artifacts, output_dir)
    print(f"校验文件：{checksum_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
