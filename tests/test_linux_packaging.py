import importlib.util
import hashlib
import os
import stat
import tarfile
import tempfile
import unittest
import subprocess
import shutil
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "packaging" / "linux" / "build_linux.py"
BUILD_WRAPPER = PROJECT_ROOT / "build_ubuntu.sh"
GIT_ATTRIBUTES = PROJECT_ROOT / ".gitattributes"
RELEASE_REQUIREMENTS = (
    PROJECT_ROOT / "requirements-release-linux.txt",
    PROJECT_ROOT / "requirements-release-windows.txt",
)


class LinuxPackagingTests(unittest.TestCase):
    def _load_module(self):
        self.assertTrue(BUILD_SCRIPT.is_file(), "缺少 Linux 构建脚本")
        spec = importlib.util.spec_from_file_location("serialtool_linux_builder", BUILD_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @unittest.skipIf(os.name == "nt", "Shell 行为在 Linux 环境验证")
    @unittest.skipUnless(shutil.which("bash"), "当前环境没有 Bash")
    def test一键脚本帮助不创建虚拟环境(self):
        self.assertTrue(BUILD_WRAPPER.is_file(), "缺少 Ubuntu 一键打包脚本")

        with tempfile.TemporaryDirectory() as temp_dir:
            venv_path = Path(temp_dir) / "build-venv"
            result = subprocess.run(
                ["bash", os.fspath(BUILD_WRAPPER), "--help"],
                env={**os.environ, "SERIALTOOL_BUILD_VENV": os.fspath(venv_path)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Ubuntu 22.04+", result.stdout)
            self.assertIn("SERIALTOOL_BUILD_VENV", result.stdout)
            self.assertFalse(venv_path.exists())

    @unittest.skipIf(os.name == "nt", "Shell 行为在 Linux 环境验证")
    @unittest.skipUnless(shutil.which("bash"), "当前环境没有 Bash")
    def test一键脚本语法有效(self):
        self.assertTrue(BUILD_WRAPPER.is_file(), "缺少 Ubuntu 一键打包脚本")
        result = subprocess.run(
            ["bash", "-n", os.fspath(BUILD_WRAPPER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        if os.name != "nt":
            self.assertTrue(BUILD_WRAPPER.stat().st_mode & stat.S_IXUSR)

    @unittest.skipIf(os.name == "nt", "Shell 行为在 Linux 环境验证")
    @unittest.skipUnless(shutil.which("bash"), "当前环境没有 Bash")
    def test一键脚本拒绝非ubuntu2204构建兼容包(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            release_file = Path(temp_dir) / "os-release"
            release_file.write_text(
                'ID=ubuntu\nVERSION_ID="24.04"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", os.fspath(BUILD_WRAPPER)],
                env={
                    **os.environ,
                    "SERIALTOOL_OS_RELEASE_FILE": os.fspath(release_file),
                },
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("Ubuntu 22.04", result.stderr)

    def test_shell脚本在windows检出时保持lf换行(self):
        self.assertTrue(GIT_ATTRIBUTES.is_file(), "缺少 .gitattributes")
        attributes = GIT_ATTRIBUTES.read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", attributes)

    def test支持amd64和arm64架构名称(self):
        builder = self._load_module()

        self.assertEqual(builder.normalize_architecture("x86_64"), ("x86_64", "amd64"))
        self.assertEqual(builder.normalize_architecture("AMD64"), ("x86_64", "amd64"))
        self.assertEqual(builder.normalize_architecture("aarch64"), ("aarch64", "arm64"))

        with self.assertRaises(ValueError):
            builder.normalize_architecture("riscv64")

    def test_pyinstaller使用linux目录模式和png图标(self):
        builder = self._load_module()
        args = builder.pyinstaller_arguments(
            project_root=Path("/src/SerialTool"),
            dist_dir=Path("/tmp/dist"),
            work_dir=Path("/tmp/build"),
            spec_dir=Path("/tmp/spec"),
        )

        self.assertIn("--onedir", args)
        self.assertIn("--windowed", args)
        self.assertIn("--noupx", args)
        self.assertIn("/src/SerialTool/图标.png:.", args)
        self.assertNotIn("--collect-submodules", args)
        self.assertNotIn("serial.tools.list_ports_windows", args)
        self.assertEqual(args[-1], "/src/SerialTool/serial_GUI.py")

    def test_deb控制文件声明ubuntu运行依赖(self):
        builder = self._load_module()

        control = builder.debian_control("1.3.5", "amd64")

        self.assertIn("Package: serialtool", control)
        self.assertIn("Version: 1.3.5", control)
        self.assertIn("Architecture: amd64", control)
        self.assertIn("libxkbcommon-x11-0", control)
        self.assertIn("libxcb-xinerama0", control)
        self.assertIn("libxcb-keysyms1", control)
        self.assertIn("libxcb-shape0", control)
        self.assertIn("libxcb-icccm4", control)
        self.assertIn("libxcb-cursor0", control)

    def test_deb布局包含启动器桌面入口和图标(self):
        builder = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "SerialTool"
            source.mkdir(parents=True)
            (source / "SerialTool").write_bytes(b"executable")
            internal_file = source / "_internal" / "library.dat"
            internal_file.parent.mkdir()
            internal_file.write_bytes(b"library")
            if os.name != "nt":
                (source / "SerialTool").chmod(0o777)
                internal_file.chmod(0o777)
            icon = root / "图标.png"
            icon.write_bytes(b"png")
            license_file = root / "LICENSE"
            license_file.write_text("license", encoding="utf-8")

            package_root = root / "package"
            builder.create_debian_layout(
                source_dir=source,
                package_root=package_root,
                version="1.3.5",
                deb_arch="amd64",
                icon_path=icon,
                license_path=license_file,
            )

            launcher = package_root / "usr" / "bin" / "serialtool"
            desktop = package_root / "usr" / "share" / "applications" / "serialtool.desktop"
            installed_icon = package_root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "serialtool.png"
            control = package_root / "DEBIAN" / "control"

            installed_executable = package_root / "opt" / "SerialTool" / "SerialTool"
            installed_internal = package_root / "opt" / "SerialTool" / "_internal" / "library.dat"
            self.assertTrue(installed_executable.is_file())
            self.assertTrue(launcher.is_file())
            if os.name != "nt":
                self.assertTrue(os.stat(launcher).st_mode & stat.S_IXUSR)
                self.assertEqual(stat.S_IMODE(installed_executable.stat().st_mode), 0o755)
                self.assertEqual(stat.S_IMODE(installed_internal.stat().st_mode), 0o644)
                self.assertEqual(stat.S_IMODE(desktop.stat().st_mode), 0o644)
                self.assertEqual(stat.S_IMODE(installed_icon.stat().st_mode), 0o644)
            self.assertIn("/opt/SerialTool/SerialTool", launcher.read_text(encoding="utf-8"))
            self.assertTrue(desktop.is_file())
            self.assertTrue(installed_icon.is_file())
            self.assertIn("Architecture: amd64", control.read_text(encoding="utf-8"))

    def test_deb在系统临时目录组装以保留unix权限(self):
        builder = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            build_root = root / "mounted-build"
            output_dir = root / "output"
            output_dir.mkdir()
            captured = {}

            def capture_layout(**kwargs):
                captured["package_root"] = kwargs["package_root"]

            with mock.patch.object(builder, "create_debian_layout", side_effect=capture_layout), mock.patch.object(
                builder.subprocess, "run"
            ):
                builder.build_deb_package(
                    bundle_dir=root / "bundle",
                    output_dir=output_dir,
                    version="1.3.5",
                    release_arch="x86_64",
                    deb_arch="amd64",
                    project_root=root,
                )

            package_root = Path(captured["package_root"])
            self.assertNotIn(build_root.resolve(), package_root.resolve().parents)

    @unittest.skipIf(os.name == "nt", "Windows 文件系统不支持 POSIX 权限断言")
    def test便携包规范化入口和普通文件权限(self):
        builder = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            internal = bundle / "_internal" / "library.dat"
            internal.parent.mkdir(parents=True)
            executable = bundle / "SerialTool"
            executable.write_bytes(b"executable")
            internal.write_bytes(b"library")
            executable.chmod(0o777)
            internal.chmod(0o777)
            output = root / "output"
            output.mkdir()

            archive = builder.build_portable_archive(
                bundle_dir=bundle,
                output_dir=output,
                version="1.3.5",
                release_arch="x86_64",
            )

            with tarfile.open(archive, "r:gz") as package:
                prefix = "SerialTool-1.3.5-ubuntu22.04-x86_64"
                self.assertEqual(package.getmember(f"{prefix}/SerialTool").mode, 0o755)
                self.assertEqual(
                    package.getmember(f"{prefix}/_internal/library.dat").mode,
                    0o644,
                )

    def test拒绝把发布目录放在待清理构建目录中(self):
        builder = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            build_root = Path(temp_dir) / "build" / "linux"
            with self.assertRaisesRegex(ValueError, "输出目录"):
                builder.validate_output_directory(
                    build_root / "release",
                    build_root,
                )

    def test相同输入生成相同便携包(self):
        builder = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            bundle.mkdir()
            executable = bundle / "SerialTool"
            executable.write_bytes(b"executable")
            first_output = root / "first"
            second_output = root / "second"
            first_output.mkdir()
            second_output.mkdir()

            first = builder.build_portable_archive(
                bundle,
                first_output,
                "1.3.5",
                "x86_64",
                source_date_epoch=123456789,
            )
            executable.touch()
            second = builder.build_portable_archive(
                bundle,
                second_output,
                "1.3.5",
                "x86_64",
                source_date_epoch=123456789,
            )

            self.assertEqual(
                hashlib.sha256(first.read_bytes()).digest(),
                hashlib.sha256(second.read_bytes()).digest(),
            )

    def test发布依赖精确锁定并包含哈希(self):
        for requirements_file in RELEASE_REQUIREMENTS:
            with self.subTest(requirements_file=requirements_file.name):
                self.assertTrue(
                    requirements_file.is_file(),
                    f"缺少发布依赖锁定文件：{requirements_file.name}",
                )
                content = requirements_file.read_text(encoding="utf-8")
                requirement_lines = [
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.startswith(("#", " ", "\\", "--"))
                ]

                self.assertTrue(requirement_lines)
                self.assertTrue(all("==" in line for line in requirement_lines))
                self.assertIn("--hash=sha256:", content)

    def test生成发布包sha256校验文件(self):
        builder = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            portable = output_dir / "portable.tar.gz"
            installer = output_dir / "serialtool.deb"
            portable.write_bytes(b"portable")
            installer.write_bytes(b"installer")

            checksum_file = builder.write_checksums(
                [portable, installer],
                output_dir,
            )
            content = checksum_file.read_text(encoding="utf-8")

            self.assertIn(hashlib.sha256(b"portable").hexdigest(), content)
            self.assertIn("portable.tar.gz", content)
            self.assertIn(hashlib.sha256(b"installer").hexdigest(), content)
            self.assertIn("serialtool.deb", content)

    def test_deb布局文件时间可固定(self):
        builder = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            (source / "SerialTool").write_bytes(b"executable")
            icon = root / "图标.png"
            icon.write_bytes(b"png")
            license_file = root / "LICENSE"
            license_file.write_text("license", encoding="utf-8")
            package_root = root / "package"

            builder.create_debian_layout(
                source,
                package_root,
                "1.3.5",
                "amd64",
                icon,
                license_file,
                source_date_epoch=123456789,
            )

            installed = package_root / "opt" / "SerialTool" / "SerialTool"
            self.assertEqual(int(installed.stat().st_mtime), 123456789)


if __name__ == "__main__":
    unittest.main()
