import unittest

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

    def test发送框默认30像素且增高窗口只扩展接收区(self):
        self.tool.show()
        self.tool.resize(900, 650)
        self.tool.text_send.setPlainText("第一行发送内容")
        self.app.processEvents()
        self.assertEqual(self.tool.text_send.verticalScrollBar().maximum(), 0)
        send_height = self.tool.text_send.height()
        self.assertEqual(send_height, 30)
        recv_height = self.tool.text_recv.height()
        self.tool.text_send.append("第二行发送内容\n第三行发送内容")
        self.app.processEvents()
        self.assertGreater(self.tool.text_send.verticalScrollBar().maximum(), 0)
        self.tool.resize(900, 750)
        self.app.processEvents()
        self.assertEqual(self.tool.text_send.height(), send_height)
        self.assertGreater(self.tool.text_recv.height(), recv_height)
