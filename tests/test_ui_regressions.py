import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QMutex, Qt
from PyQt5.QtWidgets import QApplication, QPushButton, QScrollArea, QSplitter, QWidget

from app_paths import AppPaths
from auto_reply import AutoReplyDialog
import dialogs
import serial_GUI
from gsm_debugger import GSMDebuggerDialog
from ota_center import OTAControlCenter
from serial_GUI import SerialTool
from theme import DARK_QSS, LIGHT_QSS


class _DialogParent(QWidget):
    def __init__(self, root: Path):
        super().__init__()
        self._app_paths = AppPaths(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            logs_dir=root / "logs",
            ota_dir=root / "ota",
        )
        for path in self._app_paths:
            path.mkdir(parents=True, exist_ok=True)
        self.config_file = str(root / "config" / "serial_config.json")
        self.current_theme = "dark"
        self.transport = SimpleNamespace(is_open=False)
        self.serial_mutex = QMutex()
        self.combo_encoding = SimpleNamespace(currentText=lambda: "UTF-8")

    def append_text(self, _text):
        pass

    def _apply_dialog_theme(self, _dialog):
        pass


class UIRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.parent = _DialogParent(Path(self.temp_dir.name))

    def tearDown(self):
        self.parent.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _close_dialog(dialog):
        for timer_name in ("_precondition_timer", "_sms_response_timer"):
            timer = getattr(dialog, timer_name, None)
            if timer is not None:
                timer.stop()
        dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        dialog.close()

    def test主窗口发送输入框允许由分割器扩展(self):
        source = inspect.getsource(SerialTool.init_ui)
        self.assertNotIn("self.text_send.setMaximumHeight", source)
        self.assertIn("self.io_splitter = QSplitter(Qt.Vertical)", source)

    def test多字符串面板显示时获得可用宽度(self):
        source = inspect.getsource(SerialTool.toggle_multi_send)
        self.assertIn("setSizes([700, 380])", source)
        self.assertNotIn("setSizes([900, 100])", source)

    def test主窗口保存并恢复窗口与分割器状态(self):
        save_source = inspect.getsource(SerialTool.save_config)
        load_source = inspect.getsource(SerialTool.load_config)
        for key in ("window_geometry", "main_splitter_state", "io_splitter_state"):
            self.assertIn(key, save_source)
            self.assertIn(key, load_source)

    def test_ota缺少前置条件时禁止开始升级(self):
        dialog = OTAControlCenter(self.parent)
        self.addCleanup(self._close_dialog, dialog)

        self.assertFalse(dialog._btn_start_ota.isEnabled())
        self.assertIn("选择固件", dialog._btn_start_ota.toolTip())

    def test_ota满足前置条件后允许开始升级(self):
        dialog = OTAControlCenter(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        firmware = Path(self.temp_dir.name) / "firmware.bin"
        firmware.write_bytes(b"firmware")
        self.parent.transport.is_open = True
        dialog._ip_combo.clear()
        dialog._ip_combo.addItem("192.168.1.10")

        dialog._set_firmware_path(str(firmware))
        dialog._update_start_button_state()

        self.assertTrue(dialog._btn_start_ota.isEnabled())

    def test长对话框提供小屏滚动容器(self):
        dialogs = [
            OTAControlCenter(self.parent),
            GSMDebuggerDialog(self.parent),
            AutoReplyDialog(self.parent),
        ]
        for dialog in dialogs:
            self.addCleanup(self._close_dialog, dialog)
            with self.subTest(dialog=type(dialog).__name__):
                scroll = dialog.findChild(QScrollArea)
                self.assertIsNotNone(scroll)
                self.assertTrue(scroll.widgetResizable())

    def test自动应答主要区域可由用户调整高度(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)

        splitter = dialog.findChild(QSplitter, "replySectionsSplitter")
        self.assertIsNotNone(splitter)
        self.assertEqual(splitter.orientation(), Qt.Vertical)
        self.assertEqual(splitter.count(), 3)

    def test_gsm危险操作具有明确视觉与无障碍标记(self):
        dialog = GSMDebuggerDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dangerous_labels = {"重启模块", "恢复出厂", "清空短信"}
        buttons = {
            button.text(): button
            for button in dialog.findChildren(QPushButton)
            if button.text() in dangerous_labels
        }

        self.assertEqual(set(buttons), dangerous_labels)
        for button in buttons.values():
            self.assertTrue(button.property("danger"))
            self.assertTrue(button.accessibleDescription())

    def test主题提供清晰的禁用状态与键盘焦点(self):
        for qss in (DARK_QSS, LIGHT_QSS):
            with self.subTest(theme="dark" if qss is DARK_QSS else "light"):
                self.assertIn("QPushButton:focus", qss)
                self.assertIn("QCheckBox:focus", qss)
                self.assertIn("QLineEdit:disabled", qss)
                self.assertIn('QPushButton[danger="true"]', qss)
                self.assertIn("QScrollArea {", qss)

    def test复杂对话框默认宽度为垂直滚动条预留空间(self):
        gsm = GSMDebuggerDialog(self.parent)
        reply = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, gsm)
        self.addCleanup(self._close_dialog, reply)

        self.assertGreaterEqual(gsm.width(), 760)
        self.assertGreaterEqual(reply.width(), 780)

    def test自动应答默认尺寸不出现横向滚动条(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog.show()
        self.app.processEvents()

        self.assertFalse(dialog.scroll_area.horizontalScrollBar().isVisible())

    def test_ota默认高度可直接显示完整内容(self):
        dialog = OTAControlCenter(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog.show()
        self.app.processEvents()

        self.assertGreaterEqual(dialog.height(), 800)
        self.assertFalse(dialog._scroll_area.verticalScrollBar().isVisible())

    def test自动应答默认高度可直接显示主要内容(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog.show()
        self.app.processEvents()

        self.assertGreaterEqual(dialog.height(), 760)
        self.assertFalse(dialog.scroll_area.verticalScrollBar().isVisible())

    def test主界面术语与无障碍名称保持一致(self):
        source = inspect.getsource(SerialTool.init_ui)
        self.assertIn('QCheckBox("HEX 显示")', source)
        self.assertIn('QCheckBox("HEX 发送")', source)
        self.assertIn('QCheckBox("显示时间戳")', source)
        self.assertIn("setAccessibleName", source)
        self.assertIn("setTabOrder", source)

    def test用户可见文案统一使用多字符串发送(self):
        self.assertNotIn("多字符发送", inspect.getsource(serial_GUI))
        self.assertNotIn("多字符发送", inspect.getsource(dialogs))

    def test数据分析帮助按钮具有可访问名称(self):
        from data_viewer import JsonViewerDialog

        source = inspect.getsource(JsonViewerDialog._init_ui)
        self.assertIn('self.btn_help.setAccessibleName("帮助")', source)


if __name__ == "__main__":
    unittest.main()
