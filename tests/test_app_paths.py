import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


class AppPathsTests(unittest.TestCase):
    def _load_module(self):
        spec = importlib.util.find_spec("app_paths")
        self.assertIsNotNone(spec, "缺少 app_paths 模块")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_linux默认使用xdg用户目录(self):
        app_paths = self._load_module()

        paths = app_paths.resolve_app_paths(
            platform_name="linux",
            environ={"HOME": "/home/tester"},
            cwd=Path("/opt/SerialTool"),
        )

        self.assertEqual(paths.config_dir, Path("/home/tester/.config/SerialTool"))
        self.assertEqual(paths.data_dir, Path("/home/tester/.local/share/SerialTool"))
        self.assertEqual(paths.cache_dir, Path("/home/tester/.cache/SerialTool"))
        self.assertEqual(paths.logs_dir, Path("/home/tester/.local/share/SerialTool/logs"))
        self.assertEqual(paths.ota_dir, Path("/home/tester/.local/share/SerialTool/ota_serve"))

    def test_linux遵循xdg环境变量(self):
        app_paths = self._load_module()

        paths = app_paths.resolve_app_paths(
            platform_name="linux",
            environ={
                "HOME": "/home/tester",
                "XDG_CONFIG_HOME": "/tmp/config",
                "XDG_DATA_HOME": "/tmp/data",
                "XDG_CACHE_HOME": "/tmp/cache",
            },
        )

        self.assertEqual(paths.config_dir, Path("/tmp/config/SerialTool"))
        self.assertEqual(paths.data_dir, Path("/tmp/data/SerialTool"))
        self.assertEqual(paths.cache_dir, Path("/tmp/cache/SerialTool"))

    def test_linux忽略空值和相对xdg目录(self):
        app_paths = self._load_module()

        paths = app_paths.resolve_app_paths(
            platform_name="linux",
            environ={
                "HOME": "/home/tester",
                "XDG_CONFIG_HOME": "relative-config",
                "XDG_DATA_HOME": "",
                "XDG_CACHE_HOME": "../relative-cache",
            },
        )

        self.assertEqual(paths.config_dir, Path("/home/tester/.config/SerialTool"))
        self.assertEqual(paths.data_dir, Path("/home/tester/.local/share/SerialTool"))
        self.assertEqual(paths.cache_dir, Path("/home/tester/.cache/SerialTool"))

    def test_windows使用用户可写目录(self):
        app_paths = self._load_module()
        cwd = Path("C:/SerialTool")

        paths = app_paths.resolve_app_paths(
            platform_name="win32",
            environ={
                "APPDATA": "C:/Users/tester/AppData/Roaming",
                "LOCALAPPDATA": "C:/Users/tester/AppData/Local",
            },
            cwd=cwd,
        )

        self.assertEqual(
            paths.config_dir,
            Path("C:/Users/tester/AppData/Roaming/SerialTool"),
        )
        self.assertEqual(
            paths.data_dir,
            Path("C:/Users/tester/AppData/Local/SerialTool"),
        )
        self.assertEqual(
            paths.cache_dir,
            Path("C:/Users/tester/AppData/Local/SerialTool/cache"),
        )

    def test旧工作目录配置只迁移一次(self):
        app_paths = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_dir = root / "legacy"
            legacy_dir.mkdir()
            (legacy_dir / "serial_config.json").write_text(
                '{"theme": "dark"}',
                encoding="utf-8",
            )
            (legacy_dir / "data_viewer.ini").write_text(
                "layout=old",
                encoding="utf-8",
            )
            if os.name == "nt":
                platform_name = "win32"
                environ = {
                    "APPDATA": str(root / "roaming"),
                    "LOCALAPPDATA": str(root / "local"),
                }
            else:
                platform_name = "linux"
                environ = {
                    "HOME": str(root),
                    "XDG_CONFIG_HOME": str(root / "config"),
                    "XDG_DATA_HOME": str(root / "data"),
                    "XDG_CACHE_HOME": str(root / "cache"),
                }
            paths = app_paths.resolve_app_paths(
                platform_name=platform_name,
                environ=environ,
                cwd=legacy_dir,
            )

            app_paths.ensure_user_dirs(paths)
            migrated = app_paths.migrate_legacy_user_data(paths, legacy_dir)

            self.assertEqual(migrated, 2)
            self.assertEqual(
                (paths.config_dir / "serial_config.json").read_text(encoding="utf-8"),
                '{"theme": "dark"}',
            )
            self.assertEqual(
                (paths.config_dir / "data_viewer.ini").read_text(encoding="utf-8"),
                "layout=old",
            )

            (legacy_dir / "serial_config.json").write_text(
                '{"theme": "light"}',
                encoding="utf-8",
            )
            self.assertEqual(
                app_paths.migrate_legacy_user_data(paths, legacy_dir),
                0,
            )
            self.assertEqual(
                (paths.config_dir / "serial_config.json").read_text(encoding="utf-8"),
                '{"theme": "dark"}',
            )

    def test创建全部用户可写目录(self):
        app_paths = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = app_paths.resolve_app_paths(
                platform_name="linux",
                environ={
                    "HOME": str(root),
                    "XDG_CONFIG_HOME": str(root / "config"),
                    "XDG_DATA_HOME": str(root / "data"),
                    "XDG_CACHE_HOME": str(root / "cache"),
                },
            )
            app_paths.ensure_user_dirs(paths)

            self.assertTrue(paths.config_dir.is_dir())
            self.assertTrue(paths.logs_dir.is_dir())
            self.assertTrue(paths.ota_dir.is_dir())
            self.assertTrue(paths.cache_dir.is_dir())

    def test资源路径可从指定打包目录解析(self):
        app_paths = self._load_module()
        bundle_dir = Path(os.sep) / "tmp" / "bundle"

        result = app_paths.resource_path("图标.png", bundle_dir=bundle_dir)

        self.assertEqual(result, bundle_dir / "图标.png")

    def test文件管理器接收绝对目录路径(self):
        app_paths = self._load_module()
        self.assertTrue(hasattr(app_paths, "open_directory"), "缺少目录打开能力")
        opened = []

        result = app_paths.open_directory(
            Path("relative") / "ota_serve",
            opener=lambda path: opened.append(path) or True,
        )

        self.assertTrue(result)
        self.assertEqual(opened, [(Path.cwd() / "relative" / "ota_serve").resolve()])


if __name__ == "__main__":
    unittest.main()
