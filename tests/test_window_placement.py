import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QRect, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from app_paths import AppPaths
from serial_GUI import SerialTool


class WindowPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        paths = AppPaths(*(root / name for name in ("config", "data", "cache", "logs", "ota")))
        for path in paths:
            path.mkdir()
        for patcher in (
            mock.patch("serial_GUI.resolve_app_paths", return_value=paths),
            mock.patch.object(SerialTool, "refresh_ports"),
            mock.patch.object(SerialTool, "load_config"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tool = SerialTool()
        self.addCleanup(self.close_tool)

    def close_tool(self):
        self.tool.close()
        self.tool.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def test反复校正不会增大窗口或移动标题栏(self):
        self.tool.show()
        self.app.processEvents()
        self.tool.setGeometry(40, 40, 740, 560)
        self.tool._ensure_window_visible()
        geometry = self.tool.geometry()
        for _ in range(5):
            self.tool._ensure_window_visible()
        self.assertEqual(self.tool.geometry(), geometry)

    def test校正后外框完整位于工作区内(self):
        self.tool.show()
        self.app.processEvents()
        self.tool.setGeometry(-3000, -2000, 1600, 1000)
        self.tool._ensure_window_visible()
        self.assertTrue(self.tool.screen().availableGeometry().contains(self.tool.frameGeometry()))

    def test窗口显示后自动找回屏幕外的位置(self):
        self.tool.setGeometry(-8000, -8000, 740, 560)
        self.tool.show()
        QTest.qWait(300)
        self.assertTrue(self.tool.screen().availableGeometry().contains(self.tool.frameGeometry()))

    def test工作区变化后自动找回窗口(self):
        self.tool.show()
        QTest.qWait(300)
        self.tool.move(-8000, -8000)
        screen = self.tool.screen()
        screen.availableGeometryChanged.emit(screen.availableGeometry())
        QTest.qWait(300)
        self.assertTrue(screen.availableGeometry().contains(self.tool.frameGeometry()))

    def test切屏信号后自动找回窗口(self):
        self.tool.show()
        QTest.qWait(300)
        self.tool.move(-8000, -8000)
        screen = self.tool.screen()
        self.tool.windowHandle().screenChanged.emit(screen)
        QTest.qWait(300)
        self.assertTrue(screen.availableGeometry().contains(self.tool.frameGeometry()))

    def test最大化还原后校正旧屏幕位置(self):
        self.tool.show()
        QTest.qWait(300)
        self.tool.move(-8000, -8000)
        self.tool.showMaximized()
        self.app.processEvents()
        self.tool.showNormal()
        QTest.qWait(300)
        self.assertTrue(self.tool.screen().availableGeometry().contains(self.tool.frameGeometry()))

    def test拔屏和缩放变化信号都能找回窗口(self):
        self.tool.show()
        QTest.qWait(300)
        screen = self.tool.screen()
        for signal, value in (
            (self.app.screenRemoved, screen),
            (screen.logicalDotsPerInchChanged, screen.logicalDotsPerInch()),
            (self.app.primaryScreenChanged, screen),
        ):
            with self.subTest(signal=signal):
                self.tool.move(-8000, -8000)
                signal.emit(value)
                QTest.qWait(300)
                self.assertTrue(screen.availableGeometry().contains(self.tool.frameGeometry()))

    def test显示器变化不会弹出已最小化或隐藏的窗口(self):
        self.tool.showMinimized()
        screen = self.app.primaryScreen()
        screen.availableGeometryChanged.emit(screen.availableGeometry())
        QTest.qWait(300)
        self.assertTrue(self.tool.isMinimized())
        self.tool.hide()
        screen.availableGeometryChanged.emit(screen.availableGeometry())
        QTest.qWait(300)
        self.assertFalse(self.tool.isVisible())

    def test显示器信号不破坏正常最大化和全屏状态(self):
        for show, state in (
            (self.tool.showMaximized, Qt.WindowMaximized),
            (self.tool.showFullScreen, Qt.WindowFullScreen),
        ):
            show()
            QTest.qWait(300)
            before = self.tool.geometry()
            screen = self.tool.screen()
            screen.availableGeometryChanged.emit(screen.availableGeometry())
            QTest.qWait(300)
            self.assertTrue(self.tool.windowState() & state)
            self.assertEqual(self.tool.geometry(), before)

    def test展开面板的延迟回调不修改最大化窗口(self):
        self.tool.show()
        self.app.processEvents()
        self.tool.toggle_multi_send()
        self.tool.showMaximized()
        self.app.processEvents()
        geometry = self.tool.geometry()
        self.tool._keep_multi_send_expanded_height(1200)
        self.assertEqual(self.tool.geometry(), geometry)
        self.assertTrue(self.tool.isMaximized())

    def test校正优先保留与窗口相交最多的负坐标副屏(self):
        primary = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080))
        secondary = SimpleNamespace(availableGeometry=lambda: QRect(-1800, -1000, 1600, 900))
        self.tool.setGeometry(-700, -600, 900, 600)
        with mock.patch.object(QApplication, "screens", return_value=[primary, secondary]), \
                mock.patch.object(QApplication, "screenAt", return_value=None), \
                mock.patch.object(QApplication, "primaryScreen", return_value=primary):
            self.tool._ensure_window_visible()
        self.assertTrue(secondary.availableGeometry().contains(self.tool.frameGeometry()))


if __name__ == "__main__":
    unittest.main()
