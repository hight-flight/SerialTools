import unittest
from unittest import mock

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QLabel, QPushButton

from tests import test_window_placement
from auto_reply import AutoReplyDialog
from gsm_debugger import GSMDebuggerDialog


class VisualLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_window_placement.WindowPlacementTests.setUpClass()
        cls.app = test_window_placement.WindowPlacementTests.app

    def setUp(self):
        fixture = test_window_placement.WindowPlacementTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.tool = fixture.tool

    def test窄窗口仍能阅读日志路径和多行发送内容(self):
        self.tool.show()
        self.tool.resize(900, 650)
        self.app.processEvents()
        self.assertGreaterEqual(self.tool.line_edit_save_path.width(), 160)
        self.assertGreaterEqual(
            self.tool.text_send.viewport().height(),
            self.tool.text_send.fontMetrics().height(),
        )
        self.assertGreaterEqual(self.tool.text_recv.height(), 100)

    def test普通选项初次显示和切换主题的字重一致(self):
        self.tool.show()
        self.app.processEvents()
        original = self.tool.check_repeat.font()
        self.assertFalse(original.bold())
        for theme in ("dark", "light"):
            self.tool.apply_theme(theme)
            self.app.processEvents()
            self.assertEqual(self.tool.check_repeat.font().weight(), original.weight())

    def test子窗口与主窗口使用一致的界面字体(self):
        dialog = AutoReplyDialog(self.tool)
        try:
            self.tool._apply_dialog_theme(dialog)
            dialog.ensurePolished()
            label = next(label for label in dialog.findChildren(QLabel) if label.text())
            label.ensurePolished()
            self.assertEqual(label.font().family(), "Microsoft YaHei")
            self.assertGreaterEqual(label.font().pointSizeF(), 9)
        finally:
            dialog.shutdown()
            dialog.close()
            dialog.deleteLater()

    def test缩小子窗口时关闭按钮仍完整可见(self):
        for cls in (AutoReplyDialog, GSMDebuggerDialog):
            dialog = cls(self.tool)
            self.tool._apply_dialog_theme(dialog)
            dialog.show()
            dialog.resize(560, 460)
            self.app.processEvents()
            try:
                close = next(b for b in dialog.findChildren(QPushButton) if b.text() == "关闭")
                bottom = close.mapTo(dialog, close.rect().bottomRight())
                top = close.mapTo(dialog, QPoint(0, 0))
                self.assertTrue(dialog.rect().contains(top))
                self.assertTrue(dialog.rect().contains(bottom))
            finally:
                if hasattr(dialog, "shutdown"):
                    dialog.shutdown()
                dialog.close()
                dialog.deleteLater()

    def test最小窗口中发送框和操作按钮不重叠(self):
        self.tool.show()
        self.tool.resize(720, 560)
        self.app.processEvents()
        editor_bottom = self.tool.text_send.mapTo(self.tool, self.tool.text_send.rect().bottomRight()).y()
        button_top = self.tool.btn_send.mapTo(self.tool, QPoint(0, 0)).y()
        self.assertLess(editor_bottom, button_top)
        self.assertGreaterEqual(self.tool.check_auto_reply.width(), self.tool.check_auto_reply.sizeHint().width())

    def test发送框获得合并行空间且增高窗口只扩展接收区(self):
        self.tool.show()
        self.tool.resize(900, 650)
        self.tool.text_send.setPlainText("第一行发送内容")
        self.app.processEvents()
        self.assertEqual(self.tool.text_send.verticalScrollBar().maximum(), 0)
        send_height = self.tool.text_send.height()
        self.assertEqual(send_height, 64)
        recv_height = self.tool.text_recv.height()
        self.tool.text_send.append("\n".join(f"第 {i} 行发送内容" for i in range(2, 12)))
        self.app.processEvents()
        self.assertGreater(self.tool.text_send.verticalScrollBar().maximum(), 0)
        self.tool.resize(900, 750)
        self.app.processEvents()
        self.assertEqual(self.tool.text_send.height(), send_height)
        self.assertGreater(self.tool.text_recv.height(), recv_height)

    def test选择长路径保留真实文件并显示文件名与完整提示(self):
        path = "D:/firmware/releases/controller_update_v1.4.4.bin"
        with mock.patch("PyQt5.QtWidgets.QFileDialog.getOpenFileName", return_value=(path, "")):
            self.tool.select_file_to_send()
        self.assertEqual(self.tool.selected_file_path, path)
        self.assertEqual(self.tool.file_path_edit.text(), "controller_update_v1.4.4.bin")
        self.assertEqual(self.tool.file_path_edit.toolTip(), path)
        self.assertTrue(self.tool.btn_send_file.isEnabled())
        with mock.patch("PyQt5.QtWidgets.QFileDialog.getOpenFileName", return_value=("", "")):
            self.tool.select_file_to_send()
        self.assertEqual(self.tool.selected_file_path, path)

    def test文件路径按视觉顺序获得键盘焦点(self):
        self.assertIs(self.tool.btn_select_file.nextInFocusChain(), self.tool.file_path_edit)
        self.assertIs(self.tool.file_path_edit.nextInFocusChain(), self.tool.btn_send_file)

    def test初始就绪信息跟随主题切换(self):
        self.tool.apply_theme("dark")
        self.assertIn(self.tool.theme_colors['ansi_fg']['32'].name(), self.tool.status_msg.text())
        self.tool.apply_theme("light")
        self.assertIn(self.tool.theme_colors['ansi_fg']['32'].name(), self.tool.status_msg.text())

    def test窄窗口精简辅助文字并在放大后恢复(self):
        from PyQt5.QtTest import QTest
        self.tool.show()
        self.tool.resize(720, 560)
        QTest.qWait(60)
        self.assertEqual(self.tool.check_hex_send.text(), "HEX")
        self.assertTrue(self.tool.check_hex_send.toolTip())
        self.assertFalse(self.tool.btn_send.isHidden())
        self.tool.resize(1120, 780)
        QTest.qWait(60)
        self.assertEqual(self.tool.check_hex_send.text(), "HEX 发送")
        self.assertFalse(self.tool.line_edit_save_path.isHidden())
