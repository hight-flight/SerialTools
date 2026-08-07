import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QMutex
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

from gsm_debugger import GSMDebuggerDialog


class _RecordingTransport:
    is_open = True

    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)


class _Parent(QWidget):
    def __init__(self):
        super().__init__()
        self.transport = _RecordingTransport()
        self.serial_mutex = QMutex()
        self.current_theme = "light"


class GSMDebuggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = _Parent()
        self.dialog = GSMDebuggerDialog(self.parent)

    def tearDown(self):
        self.dialog.close()
        self.parent.close()

    def test_cmgl头部后的下一行作为短信正文(self):
        self.dialog._parse_response(
            '+CMGL: 1,"REC READ","+8613800138000","","26/08/07,12:34:56+32"'
        )
        self.dialog._parse_response("包含,逗号的短信正文")

        self.assertEqual(self.dialog.table_sms.rowCount(), 1)
        self.assertEqual(self.dialog.table_sms.item(0, 2).text(), "26/08/07,12:34:56+32")
        self.assertEqual(self.dialog.table_sms.item(0, 4).text(), "包含,逗号的短信正文")

    def test_cmgl空正文不会把ok误当作短信内容(self):
        response = (
            '+CMGL: 2,"REC READ","+8613800138000","","26/08/07,12:35:00+32"'
            "\r\n\r\nOK\r\n"
        ).encode("utf-8")

        self.dialog._process_receive_data(response)

        self.assertEqual(self.dialog.table_sms.rowCount(), 1)
        self.assertEqual(self.dialog.table_sms.item(0, 4).text(), "")
        self.assertIsNone(self.dialog._pending_cmgl)

    def test短信发送不会依赖固定延时盲发后续命令(self):
        self.dialog.edit_sms_phone.setText("13800138000")
        self.dialog.edit_sms_content.setText("中文短信")

        self.dialog._send_sms()
        QTest.qWait(900)

        self.assertEqual(self.parent.transport.writes, [b"AT+CMGF=1\r\n"])

    def test短信发送按ok和提示符推进状态(self):
        self.dialog.edit_sms_phone.setText("13800138000")
        self.dialog.edit_sms_content.setText("中文短信")
        self.dialog._send_sms()

        self.dialog._process_receive_data(b"OK\r\n")
        self.assertEqual(
            self.parent.transport.writes[-1],
            b'AT+CMGS="13800138000"\r\n',
        )

        self.dialog._process_receive_data(b"> ")
        self.assertEqual(self.parent.transport.writes[-1], "中文短信".encode("utf-8") + b"\x1A")

    def test短信响应超时后恢复空闲状态(self):
        self.dialog.edit_sms_phone.setText("13800138000")
        self.dialog.edit_sms_content.setText("中文短信")
        self.dialog._send_sms()

        self.assertTrue(self.dialog._sms_response_timer.isActive())
        self.dialog._sms_response_timer.timeout.emit()

        self.assertEqual(self.dialog._sms_send_state, "idle")
        self.assertIsNone(self.dialog._pending_sms)


if __name__ == "__main__":
    unittest.main()
