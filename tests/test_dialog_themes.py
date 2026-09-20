import unittest
import ctypes
from unittest import mock
from types import SimpleNamespace

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QGroupBox, QInputDialog, QDialog

from tests import test_multi_send
from auto_reply import AutoReplyDialog
from ota_center import OTAControlCenter
from data_viewer import ProtocolEditorDialog
import pyqtgraph as pg


class DialogThemeTests(unittest.TestCase):
    setUp = test_multi_send.MultiSendTests.setUp
    tearDown = test_multi_send.MultiSendTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test子窗口分组使用主窗口面板样式(self):
        dialog = AutoReplyDialog(self.tool)
        self.addCleanup(dialog.shutdown)
        self.tool._apply_dialog_theme(dialog)
        groups = dialog.findChildren(QGroupBox)
        self.assertTrue(groups)
        for group in groups:
            self.assertTrue(group.property("workspaceCard"))

    def test示波器切换主题时更新背景且保留采样(self):
        self.tool.apply_theme("dark")
        self.tool.transport = SimpleNamespace(is_open=True, close=lambda: None)
        self.tool.oscilloscope()
        dialog = self.tool._scope_dialog
        self.addCleanup(dialog.close)
        plot = dialog.findChild(pg.PlotWidget)
        curve = plot.listDataItems()[0]
        curve.setData([0, 1], [12, 24])
        dark_pen = curve.opts["pen"].color().name()
        self.tool.apply_theme("light")
        self.assertEqual(plot.backgroundBrush().color().name(), "#ffffff")
        self.assertNotEqual(curve.opts["pen"].color().name(), dark_pen)
        self.assertEqual(list(curve.getData()[1]), [12, 24])
        self.tool.apply_theme("dark")
        self.assertEqual(plot.backgroundBrush().color().name(), "#19212c")

    def test自动应答编辑提示跟随主题(self):
        dialog = AutoReplyDialog(self.tool)
        self.addCleanup(dialog.shutdown)
        self.tool._apply_dialog_theme(dialog)
        colors = []
        for mode in ("dark", "light"):
            self.tool.apply_theme(mode)
            dialog.lbl_edit_mode.ensurePolished()
            colors.append(dialog.lbl_edit_mode.palette().color(QPalette.WindowText).name())
        self.assertNotEqual(*colors)

    def testOTA状态颜色跟随主题且不改变升级状态(self):
        dialog = OTAControlCenter(self.tool)
        self.addCleanup(dialog.close)
        self.tool._apply_dialog_theme(dialog)
        for state in (dialog.STATE_SUCCESS, dialog.STATE_FAILED, dialog.STATE_UNCONFIRMED):
            dialog._set_state(state, "测试状态")
            colors = []
            for mode in ("dark", "light"):
                self.tool.apply_theme(mode)
                dialog._state_label.ensurePolished()
                colors.append(dialog._state_label.palette().color(QPalette.WindowText).name())
                self.assertEqual(dialog.ota_state, state)
                self.assertEqual(dialog._state_label.text(), "测试状态")
            self.assertNotEqual(*colors)

    def test协议编辑器已有错误单元格跟随主题(self):
        dialog = ProtocolEditorDialog(self.tool, is_dark=True)
        self.addCleanup(dialog.close)
        self.tool._apply_dialog_theme(dialog)
        dialog._add_field_row()
        item = dialog.fields_table.item(0, 2)
        item.setText("-1")
        self.tool.apply_theme("light")
        self.assertEqual(item.background().color().name(), "#fde7ea")
        self.tool.apply_theme("dark")
        self.assertEqual(item.background().color().name(), "#5c2b31")

    def testOTA禁用操作按钮显示禁用配色(self):
        dialog = OTAControlCenter(self.tool)
        self.addCleanup(dialog.close)
        self.tool._apply_dialog_theme(dialog)
        for mode in ("dark", "light"):
            self.tool.apply_theme(mode)
            for button in (dialog._btn_start_ota, dialog._btn_stop_ota):
                button.setEnabled(True)
                enabled = button.palette().color(QPalette.Button).name()
                button.setEnabled(False)
                disabled = button.palette().color(QPalette.Button).name()
                self.assertNotEqual(enabled, disabled)

    def test原生Qt输入窗口登记并跟随主题切换(self):
        self.tool.apply_theme("dark")
        dialog = QInputDialog(self.tool)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        self.assertIn(dialog, self.tool._themed_dialogs)
        self.tool.apply_theme("light")
        self.assertEqual(dialog.styleSheet(), "")

    def testWindows标题栏切换同步背景文字并重绘边框(self):
        import theme
        dialog = QDialog(self.tool)
        self.addCleanup(dialog.close)
        attributes = {}

        def set_attribute(hwnd, attr, pointer, size):
            key = getattr(attr, "value", attr)
            attributes[key] = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32)).contents.value
            return 0

        dwm = SimpleNamespace(DwmSetWindowAttribute=mock.Mock(side_effect=set_attribute))
        user = SimpleNamespace(RedrawWindow=mock.Mock(return_value=1))
        with mock.patch("theme.platform.system", return_value="Windows"), \
                mock.patch.object(ctypes, "windll", SimpleNamespace(dwmapi=dwm, user32=user), create=True), \
                mock.patch.object(dialog, "winId", return_value=0x123456789):
            theme.apply_dialog_theme(dialog, True)
            self.assertEqual(attributes.get(20), 1)
            self.assertEqual(attributes.get(35), 0x221A15)
            self.assertEqual(attributes.get(36), 0xF5EDE6)
            self.assertTrue(user.RedrawWindow.called)
            theme.apply_dialog_theme(dialog, False)
            self.assertEqual(attributes.get(20), 0)
            self.assertEqual(attributes.get(35), 0xFFFFFFFF)
            self.assertEqual(attributes.get(36), 0xFFFFFFFF)

    def test原生句柄销毁事件不重新创建窗口(self):
        dialog = QDialog(self.tool)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.testAttribute(Qt.WA_WState_Created))
        with mock.patch.object(self.tool, "_apply_dialog_theme") as apply_theme:
            self.tool.eventFilter(dialog, QEvent(QEvent.WinIdChange))
            apply_theme.assert_not_called()

    def test关闭子窗口后不执行延迟标题栏刷新(self):
        dialog = QDialog(self.tool)
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.close()
        with mock.patch("serial_GUI.apply_window_titlebar") as apply_titlebar:
            self.app.processEvents()
            apply_titlebar.assert_not_called()
