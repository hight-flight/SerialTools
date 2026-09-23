import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from serial_GUI import SerialTool


class FileSendCancelUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test文件发送区显示独立取消按钮且初始不可用(self):
        tool = SerialTool()
        self.addCleanup(tool.close)

        self.assertEqual(tool.btn_cancel_file_send.text(), "取消发送")
        self.assertFalse(tool.btn_cancel_file_send.isEnabled())


if __name__ == "__main__":
    unittest.main()
