import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QMutex, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QHeaderView, QMessageBox, QPushButton, QScrollArea,
    QSplitter, QStyle, QStyleOptionButton, QTableWidget, QWidget,
)

from app_paths import AppPaths
from auto_reply import AutoReplyDialog
import dialogs
import serial_GUI
import theme
from gsm_debugger import GSMDebuggerDialog
from ota_center import OTAControlCenter
from serial_GUI import SerialTool
from theme import DARK_QSS, LIGHT_QSS, VERSION, apply_dialog_theme


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
        self.check_auto_reply = QCheckBox("自动应答")

    def append_text(self, _text):
        pass

    def _apply_dialog_theme(self, _dialog):
        pass

    def _ensure_auto_reply_dialog(self):
        return SerialTool._ensure_auto_reply_dialog(self)

    def _sync_auto_reply_checkbox(self, checked):
        SerialTool._sync_auto_reply_checkbox(self, checked)


class UIRegressionTests(unittest.TestCase):
    def test应用版本号为1_4_0(self):
        self.assertEqual(VERSION, "1.4.0")

    def test应用在创建窗口前启用高DPI图像(self):
        self.assertTrue(
            QApplication.testAttribute(Qt.AA_EnableHighDpiScaling)
        )
        self.assertTrue(
            QApplication.testAttribute(Qt.AA_UseHighDpiPixmaps)
        )

    def test主窗口与子窗口使用方形多分辨率图标(self):
        tool = SerialTool()
        self.addCleanup(self._close_dialog, tool)
        icon_sizes = {
            (size.width(), size.height()) for size in tool.windowIcon().availableSizes()
        }
        self.assertTrue({(16, 16), (32, 32), (48, 48)}.issubset(icon_sizes))
        self.assertEqual(tool.windowIcon().pixmap(32, 32).size().width(), 32)
        self.assertEqual(tool.windowIcon().pixmap(32, 32).size().height(), 32)

        child = dialogs.show_crc_calculator(tool, is_dark=True)
        self.addCleanup(self._close_dialog, child)
        child_pixmap = child.windowIcon().pixmap(32, 32)
        self.assertEqual((child_pixmap.width(), child_pixmap.height()), (32, 32))
        app_pixmap = QApplication.instance().windowIcon().pixmap(32, 32)
        self.assertEqual((app_pixmap.width(), app_pixmap.height()), (32, 32))

    def test全局勾选框在两种主题下都显示勾选标记(self):
        self.assertIn("__CHECK_DARK__", DARK_QSS)
        self.assertIn("__CHECK_LIGHT__", LIGHT_QSS)

    def test确认弹窗按钮根据文字保留完整宽度(self):
        self.assertTrue(hasattr(theme, "fit_message_box_buttons"))
        box = QMessageBox(
            QMessageBox.Question,
            "确认",
            "是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            self.parent,
        )
        self.addCleanup(self._close_dialog, box)
        for button in box.buttons():
            button.setMinimumWidth(0)

        theme.fit_message_box_buttons(box)

        for button in box.buttons():
            text_width = button.fontMetrics().horizontalAdvance(
                button.text().replace("&", "")
            )
            self.assertGreaterEqual(button.minimumWidth(), text_width + 32)
        self.assertIn(
            "fit_message_box_buttons(dialog)",
            inspect.getsource(theme.apply_dialog_theme),
        )
        self.assertIn(
            "fit_message_box_buttons(msgbox)",
            inspect.getsource(SerialTool._patch_qmessagebox_theme),
        )

    def test所有普通按钮为文字保留安全余量(self):
        self.assertTrue(hasattr(theme, "fit_push_button_texts"))
        button = QPushButton("隐藏多字符串发送", self.parent)
        text_width = button.fontMetrics().horizontalAdvance(button.text())
        button.resize(text_width, 28)
        button.setMinimumWidth(text_width)

        theme.fit_push_button_texts(button)
        button.resize(button.minimumSizeHint())
        option = QStyleOptionButton()
        button.initStyleOption(option)
        content_rect = button.style().subElementRect(
            QStyle.SE_PushButtonContents, option, button
        )

        self.assertGreaterEqual(content_rect.width(), text_width + 8)
        self.assertIn(
            "fit_push_button_texts(dialog)",
            inspect.getsource(theme.apply_dialog_theme),
        )
        self.assertIn(
            "fit_push_button_texts(self)",
            inspect.getsource(SerialTool.apply_theme),
        )
        self.assertIn(
            "fit_push_button_texts(obj)",
            inspect.getsource(SerialTool.eventFilter),
        )

    def test主界面紧凑控件不使用固定高度(self):
        source = inspect.getsource(SerialTool.init_ui)
        self.assertNotIn("setFixedHeight(26)", source)
        self.assertIn("min(720, available.width())", source)

    def test子窗口统一登记以支持运行时切换主题(self):
        source = inspect.getsource(SerialTool._apply_dialog_theme)
        self.assertIn("_track_dialog", source)
        refresh_source = inspect.getsource(SerialTool._refresh_open_dialog_themes)
        self.assertIn("set_theme", refresh_source)

    def test保存路径提供完整提示并从头显示(self):
        source = inspect.getsource(SerialTool._show_save_directory)
        self.assertIn("setToolTip", source)
        self.assertIn("setCursorPosition(0)", source)

    def test示波器单实例且空状态会重新挂载(self):
        source = inspect.getsource(SerialTool.oscilloscope)
        self.assertIn("self._scope_dialog", source)
        self.assertIn("availableGeometry", source)
        self.assertIn("plot_widget.addItem(empty_text)", source)
        self.assertGreater(
            source.rfind("plot_widget.addItem(empty_text)"),
            source.find("def rebuild_channels"),
        )

    def test工具子窗口返回实例供主窗口跟踪(self):
        for helper in (
            dialogs.show_crc_calculator,
            dialogs.show_hex_converter,
            dialogs.show_serial_monitor,
        ):
            self.assertIn("return dialog", inspect.getsource(helper))

    def test_hex转换器支持十进制反向转换(self):
        source = inspect.getsource(dialogs.show_hex_converter)
        self.assertIn("def on_decimal_changed", source)
        self.assertIn("dec_edit.textEdited.connect(on_decimal_changed)", source)

    def test简单工具窗按内容收紧不留大片空白(self):
        crc = dialogs.show_crc_calculator(self.parent, is_dark=True)
        converter = dialogs.show_hex_converter(self.parent, is_dark=True)
        self.addCleanup(self._close_dialog, crc)
        self.addCleanup(self._close_dialog, converter)
        self.app.processEvents()

        self.assertLessEqual(crc.height(), 260)
        self.assertLessEqual(converter.height(), 180)

    def test串口监视器长字段提供完整提示(self):
        source = inspect.getsource(dialogs.show_serial_monitor)
        self.assertIn("setToolTip", source)
        self.assertIn("ResizeToContents", source)

    def test复杂子窗口初始尺寸不超出当前屏幕(self):
        for initializer in (
            OTAControlCenter.init_ui,
            AutoReplyDialog._init_ui,
            GSMDebuggerDialog._init_ui,
        ):
            source = inspect.getsource(initializer)
            self.assertIn("availableGeometry", source)

    def test_ota主操作按钮使用统一语义属性(self):
        dialog = OTAControlCenter(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        self.assertEqual(dialog._btn_start_ota.property("primary"), True)
        self.assertEqual(dialog._btn_stop_ota.property("danger"), True)

    def test_gsm短信表格长内容不会无提示截断(self):
        dialog = GSMDebuggerDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        source = inspect.getsource(GSMDebuggerDialog._append_sms_row)
        self.assertIn("setToolTip", source)
        header = dialog.table_sms.horizontalHeader()
        self.assertEqual(header.sectionResizeMode(4), QHeaderView.Stretch)

    def test自动应答规则表格保留完整内容提示(self):
        source = inspect.getsource(AutoReplyDialog._refresh_rules_table)
        self.assertIn("setToolTip", source)

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

    def test主界面自动应答勾选框位于重复发送间隔之后(self):
        source = inspect.getsource(SerialTool.init_ui)
        repeat_pos = source.index("repeat_layout.addWidget(self.check_repeat)")
        auto_reply_pos = source.find("repeat_layout.addWidget(self.check_auto_reply)")
        interval_pos = source.index("repeat_layout.addWidget(self.spin_interval)")

        self.assertGreaterEqual(auto_reply_pos, 0)
        self.assertLess(repeat_pos, interval_pos)
        self.assertLess(interval_pos, auto_reply_pos)

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

    def test自动应答编辑区紧凑且日志获得更多高度(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog.show()
        self.app.processEvents()

        splitter = dialog.findChild(QSplitter, "replySectionsSplitter")
        table_height, editor_height, log_height = splitter.sizes()
        self.assertGreater(log_height, editor_height)
        self.assertGreaterEqual(dialog.text_log.minimumHeight(), 150)
        self.assertLessEqual(
            abs(
                dialog.btn_save_edit.geometry().center().y()
                - dialog.check_enabled.geometry().center().y()
            ),
            4,
        )

    def test自动应答默认布局压缩编辑区并增大日志区(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog.show()
        self.app.processEvents()

        sizes = dialog.reply_sections_splitter.sizes()
        self.assertLess(sizes[1], sizes[2])
        self.assertGreaterEqual(sizes[2], 180)

    def test自动应答初始空状态与规则按钮状态明确(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)

        self.assertFalse(dialog.check_enable.isChecked())
        self.assertEqual(dialog.table_rules.rowCount(), 1)
        self.assertIn("暂无规则", dialog.table_rules.item(0, 0).text())
        self.assertTrue(dialog.table_rules.verticalHeader().isHidden())
        self.assertFalse(dialog.btn_edit_rule.isEnabled())
        self.assertFalse(dialog.btn_delete_rule.isEnabled())
        self.assertFalse(dialog.btn_move_up.isEnabled())
        self.assertFalse(dialog.btn_move_down.isEnabled())

    def test自动应答窗口关闭后仍处理已启用的规则(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog._send_response = mock.Mock()
        dialog._rules = [{
            "trigger": "PING", "match_mode": "文本完全", "response": "PONG",
            "response_format": "文本", "enabled": True, "max_count": 0,
            "newline": False, "count": 0,
        }]
        dialog._clean_state = dialog._serialize_persistent_state()
        dialog.check_enable.setChecked(True)

        dialog.show()
        self.app.processEvents()
        dialog.close()
        self.app.processEvents()
        dialog.handle_receive_data(b"PING")

        self.assertFalse(dialog.isVisible())
        self.assertFalse(dialog._closing)
        dialog._send_response.assert_called_once_with(dialog._rules[0])

    def test自动应答隐藏期间切换主题后重新打开使用当前主题(self):
        self.addCleanup(self.app.setStyleSheet, "")
        self.app.setStyleSheet(DARK_QSS)
        self.parent._apply_dialog_theme = lambda dialog: apply_dialog_theme(
            dialog, is_dark=(self.parent.current_theme == "dark")
        )

        SerialTool.show_auto_reply(self.parent)
        dialog = self.parent._auto_reply_dialog
        self.addCleanup(dialog.shutdown)
        self.assertTrue(dialog.styleSheet())
        dialog.close()
        self.app.processEvents()

        self.parent.current_theme = "light"
        SerialTool.show_auto_reply(self.parent)

        self.assertEqual(dialog.styleSheet(), "")

    def test自动应答存在未保存修改时可以取消主窗口退出(self):
        dialog = AutoReplyDialog(self.parent)
        dialog.show()
        self.app.processEvents()
        dialog.spin_delay.setValue(dialog.spin_delay.value() + 1)

        def cleanup():
            dialog._clean_state = dialog._serialize_persistent_state()
            dialog.shutdown()

        self.addCleanup(cleanup)
        with mock.patch.object(
                serial_GUI.QMessageBox, "question", return_value=QMessageBox.No):
            result = dialog.shutdown()

        self.assertIs(result, False)
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog._closing)

    def test自动应答窗口隐藏后退出仍保护未保存修改(self):
        dialog = AutoReplyDialog(self.parent)
        dialog.show()
        self.app.processEvents()
        dialog.spin_delay.setValue(dialog.spin_delay.value() + 1)

        def cleanup():
            dialog._clean_state = dialog._serialize_persistent_state()
            dialog.shutdown()

        self.addCleanup(cleanup)
        with mock.patch.object(
                serial_GUI.QMessageBox, "question", return_value=QMessageBox.Yes):
            dialog.close()
        self.assertFalse(dialog.isVisible())

        with mock.patch.object(
                serial_GUI.QMessageBox, "question", return_value=QMessageBox.No):
            result = dialog.shutdown()

        self.assertIs(result, False)
        self.assertFalse(dialog._closing)

    def test主窗口在自动应答取消退出时停止后续关闭流程(self):
        auto_reply_dialog = SimpleNamespace(shutdown=lambda: False)
        window = SimpleNamespace(
            _auto_reply_dialog=auto_reply_dialog,
            save_config=mock.Mock(), cleanup_resources=mock.Mock(),
            config_file=str(Path(self.temp_dir.name) / "missing.json"),
        )
        event = mock.Mock()

        SerialTool.closeEvent(window, event)

        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        window.save_config.assert_not_called()
        window.cleanup_resources.assert_not_called()

    def test自动应答主要操作名称与位置清晰(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog.show()
        self.app.processEvents()
        labels = {button.text() for button in dialog.findChildren(QPushButton)}

        self.assertIn("添加到列表", labels)
        self.assertIn("导出规则", labels)
        self.assertIn("导入规则", labels)
        self.assertIn("清空规则", labels)
        self.assertNotIn("添加规则", labels)
        self.assertNotIn("保存规则", labels)
        self.assertNotIn("加载规则", labels)
        self.assertGreater(
            dialog.btn_clear_rules.geometry().left(),
            dialog.btn_delete_rule.geometry().left(),
        )

    def test自动应答规则序号列使用紧凑固定宽度(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        header = dialog.table_rules.horizontalHeader()

        self.assertEqual(header.sectionResizeMode(0), QHeaderView.Fixed)
        self.assertEqual(dialog.table_rules.columnWidth(0), 54)

    def test自动应答关键输入具有明确无障碍名称(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)

        self.assertEqual(dialog.spin_delay.accessibleName(), "响应延迟")
        self.assertEqual(dialog.spin_max_count.accessibleName(), "最大响应次数")
        self.assertEqual(dialog.table_rules.accessibleName(), "自动应答规则列表")

    def test自动应答日志最多保留2000行(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)

        self.assertEqual(dialog.text_log.document().maximumBlockCount(), 2000)

    def test自动应答日志在100毫秒内批量刷新(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)

        dialog._append_log("第一条")
        dialog._append_log("第二条")
        self.assertEqual(dialog.text_log.toPlainText(), "")

        QTest.qWait(150)

        log_text = dialog.text_log.toPlainText()
        self.assertIn("第一条", log_text)
        self.assertIn("第二条", log_text)

    def test清空自动应答日志同时丢弃待刷新内容(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)

        dialog._append_log("不应重新出现")
        dialog._clear_log()
        QTest.qWait(150)

        self.assertEqual(dialog.text_log.toPlainText(), "")

    def test自动应答退出时释放待刷新日志(self):
        dialog = AutoReplyDialog(self.parent)
        dialog._append_log("退出时丢弃")

        self.assertTrue(dialog.shutdown())

        self.assertFalse(dialog._log_flush_timer.isActive())
        self.assertEqual(len(dialog._pending_log_lines), 0)

    def test自动应答待刷新日志最多保留2000条(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)

        for index in range(2500):
            dialog._append_log(f"日志{index}")
        dialog._log_flush_timer.stop()

        self.assertEqual(len(dialog._pending_log_lines), 2000)
        self.assertNotIn("日志0", dialog._pending_log_lines[0])
        self.assertIn("日志2499", dialog._pending_log_lines[-1])

    def test自动应答无效十六进制输入即时阻止提交(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog.edit_trigger.setText("GG")
        dialog.edit_response.setText("OK")
        dialog.combo_match_mode.setCurrentText("HEX匹配")

        self.assertFalse(dialog.btn_save_edit.isEnabled())
        self.assertIn("触发条件", dialog.lbl_validation.text())

        dialog.edit_trigger.setText("01 03")
        self.assertTrue(dialog.btn_save_edit.isEnabled())
        self.assertEqual(dialog.lbl_validation.text(), "")

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

    def test工业控制台主题使用确认的视觉令牌(self):
        """明暗主题应使用确认预览稿的页面、主操作和数据面板颜色。"""
        self.assertIn("#F6F8FB", LIGHT_QSS)
        self.assertIn("#1677FF", LIGHT_QSS)
        self.assertIn("#151A22", DARK_QSS)
        self.assertIn("#49A6FF", DARK_QSS)
        self.assertIn("QPlainTextEdit", DARK_QSS)

    def test暗色原生标题栏使用石墨背景(self):
        source = inspect.getsource(SerialTool.apply_theme)
        self.assertIn("_set_titlebar_dark(True, color_hex='#151A22')", source)

    def test暗色子窗口原生标题栏使用石墨背景(self):
        source = inspect.getsource(apply_dialog_theme)
        self.assertIn("r, g, b = 0x15, 0x1A, 0x22", source)

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

        available_height = dialog.screen().availableGeometry().height()
        self.assertEqual(dialog.height(), min(800, available_height))
        if available_height >= 800:
            self.assertFalse(dialog._scroll_area.verticalScrollBar().isVisible())

    def test自动应答默认高度可直接显示主要内容(self):
        dialog = AutoReplyDialog(self.parent)
        self.addCleanup(self._close_dialog, dialog)
        dialog.show()
        self.app.processEvents()

        available_height = dialog.screen().availableGeometry().height()
        self.assertEqual(dialog.height(), min(760, available_height))
        if available_height >= 760:
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

    def test多字符串表头与限定轮数字体一致且面板留有余量(self):
        source = inspect.getsource(serial_GUI.SerialTool.init_ui)
        self.assertIn('header.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))', source)
        self.assertIn('header.setFixedHeight(34)', source)
        self.assertIn('header.setDefaultAlignment(Qt.AlignCenter)', source)
        self.assertIn('self.table_multi_send.setColumnWidth(MULTI_COL_HEX, 60)', source)
        self.assertIn('self.table_multi_send.setColumnWidth(MULTI_COL_TEXT, 72)', source)
        self.assertIn('self.table_multi_send.setColumnWidth(MULTI_COL_NAME, 64)', source)
        self.assertIn('self.table_multi_send.setColumnWidth(MULTI_COL_DELAY, 144)', source)
        self.assertIn('self.table_multi_send.setMinimumWidth(456)', source)

    def test关键表格表头按当前样式完整显示(self):
        tool = SerialTool()
        monitor = dialogs.show_serial_monitor(self.parent, is_dark=True)
        reply = AutoReplyDialog(self.parent)
        for dialog in (tool, monitor, reply):
            self.addCleanup(self._close_dialog, dialog)
            dialog.show()
        QApplication.processEvents()

        expected_headers = (
            (tool, {"HEX", "备注"}),
            (monitor, {"序号", "端口", "描述", "硬件ID", "制造商", "VID/PID"}),
            (reply, {"序号"}),
        )
        for window, names in expected_headers:
            tables = window.findChildren(QTableWidget)
            found = set()
            for table in tables:
                header = table.horizontalHeader()
                for column in range(table.columnCount()):
                    item = table.horizontalHeaderItem(column)
                    if item is None or item.text() not in names:
                        continue
                    found.add(item.text())
                    required_width = (
                        header.fontMetrics().horizontalAdvance(item.text()) + 8
                    )
                    self.assertGreaterEqual(
                        header.sectionSize(column),
                        required_width,
                        f"{item.text()} 表头仍会被裁切",
                    )
            self.assertEqual(found, names)

    def test多字符串HEX勾选框跟随暗色主题色(self):
        source = inspect.getsource(serial_GUI.FullHitCheckBox.paintEvent)
        self.assertIn('getattr(self.window(), "current_theme", "light") == "dark"', source)
        self.assertIn('QColor("#49A6FF")', source)
        self.assertIn('QColor("#19212C")', source)

    def test多字符串表格内容使用常规紧凑字体(self):
        source = inspect.getsource(serial_GUI.SerialTool.init_ui)
        self.assertIn(
            'self.table_multi_send.setFont(QFont("Microsoft YaHei", 8, QFont.Normal))',
            source,
        )
        row_source = inspect.getsource(serial_GUI.SerialTool._insert_multi_item_row)
        self.assertIn('send_button.setFont(QFont("Microsoft YaHei", 8, QFont.Normal))', row_source)
        self.assertIn('delay_spin.setFont(QFont("Consolas", 8, QFont.Normal))', row_source)

    def test数据分析帮助按钮具有可访问名称(self):
        from data_viewer import JsonViewerDialog

        source = inspect.getsource(JsonViewerDialog._init_ui)
        self.assertIn('self.btn_help.setAccessibleName("帮助")', source)


if __name__ == "__main__":
    unittest.main()
