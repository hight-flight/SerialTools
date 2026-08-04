import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_app


class WindowsPackagingTests(unittest.TestCase):
    def test清理只删除已验证项目内的构建目录(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            project_root.mkdir()
            (project_root / "serial_GUI.py").write_text("", encoding="utf-8")
            (project_root / "build").mkdir()
            (project_root / "dist").mkdir()
            (project_root / "SerialTool.spec").write_text("", encoding="utf-8")

            build_app.clear_old_build(project_root)

            self.assertFalse((project_root / "build").exists())
            self.assertFalse((project_root / "dist").exists())
            self.assertFalse((project_root / "SerialTool.spec").exists())

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


if __name__ == "__main__":
    unittest.main()
