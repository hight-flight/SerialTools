import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_app


class WindowsPackagingTests(unittest.TestCase):
    def test默认模式生成windows便携版和安装包(self):
        options = build_app.parse_cli_args([])

        self.assertTrue(options.onedir)
        self.assertTrue(options.setup)

    def test可显式选择旧版单文件模式(self):
        options = build_app.parse_cli_args(["--onefile"])

        self.assertFalse(options.onedir)
        self.assertFalse(options.setup)

    def test可只生成windows便携版(self):
        options = build_app.parse_cli_args(["--onedir"])

        self.assertTrue(options.onedir)
        self.assertFalse(options.setup)

    def test默认模式缺少安装包时返回失败(self):
        options = mock.Mock(onedir=True, setup=True)
        with mock.patch.object(build_app, "parse_cli_args", return_value=options), \
                mock.patch.object(build_app, "clear_old_build"), \
                mock.patch.object(build_app, "check_dependencies", return_value=True), \
                mock.patch.object(build_app, "find_icon", return_value=None), \
                mock.patch.object(build_app, "verify_main_script", return_value=True), \
                mock.patch.object(build_app, "build_application", return_value=True), \
                mock.patch.object(build_app, "verify_build", return_value=True), \
                mock.patch.object(build_app, "build_setup", return_value=False):
            result = build_app.main()

        self.assertEqual(result, 1)

    def test清理只删除已验证项目内的构建目录(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            project_root.mkdir()
            (project_root / "serial_GUI.py").write_text("", encoding="utf-8")
            (project_root / "build" / "SerialTool").mkdir(parents=True)
            linux_build = project_root / "build" / "linux"
            linux_build.mkdir()
            (linux_build / "keep.txt").write_text("保留", encoding="utf-8")
            (project_root / "dist" / "SerialTool").mkdir(parents=True)
            linux_dist = project_root / "dist" / "linux"
            linux_dist.mkdir()
            (linux_dist / "keep.txt").write_text("保留", encoding="utf-8")
            (project_root / "dist" / "SerialTool.exe").write_bytes(b"exe")
            (project_root / "SerialTool.spec").write_text("", encoding="utf-8")

            build_app.clear_old_build(project_root)

            self.assertFalse((project_root / "build" / "SerialTool").exists())
            self.assertFalse((project_root / "dist" / "SerialTool").exists())
            self.assertFalse((project_root / "dist" / "SerialTool.exe").exists())
            self.assertFalse((project_root / "SerialTool.spec").exists())
            self.assertTrue((linux_build / "keep.txt").is_file())
            self.assertTrue((linux_dist / "keep.txt").is_file())

    def test缺少项目入口时拒绝清理(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            unsafe_root = Path(temp_dir)
            protected = unsafe_root / "dist"
            protected.mkdir()
            marker = protected / "keep.txt"
            marker.write_text("保留", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "项目"):
                build_app.clear_old_build(unsafe_root)

            self.assertTrue(marker.is_file())

    def test未安装upx时不自动下载(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("shutil.which", return_value=None), mock.patch(
                "urllib.request.urlretrieve"
            ) as download:
                result = build_app.find_upx(Path(temp_dir))

            self.assertEqual(result, (False, None))
            download.assert_not_called()

    def test本地配置文件不进入发布参数(self):
        args = build_app.build_pyinstaller_arguments(
            icon_path=Path("图标.ico"),
            onedir=True,
        )

        joined = " ".join(str(item) for item in args)
        self.assertNotIn("serial_config.json", joined)
        self.assertIn("serial_GUI.py", joined)

    def test_windows发布不收集无关平台和全部pyqtgraph子模块(self):
        args = build_app.build_pyinstaller_arguments(onedir=True)
        joined = " ".join(str(item) for item in args)

        self.assertNotIn("--collect-submodules pyqtgraph", joined)
        self.assertNotIn("serial.tools.list_ports_linux", joined)
        self.assertNotIn("serial.tools.list_ports_osx", joined)
        self.assertIn("serial.tools.list_ports_windows", joined)

    def test_inno脚本生成到指定构建目录(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "installer"
            script = Path(
                build_app.generate_iss(
                    "1.3.5",
                    icon_path=None,
                    output_dir=output_dir,
                )
            )

            self.assertEqual(script.parent, output_dir)
            content = script.read_text(encoding="utf-8-sig")
            self.assertIn('#define MyAppVersion "1.3.5"', content)


if __name__ == "__main__":
    unittest.main()
