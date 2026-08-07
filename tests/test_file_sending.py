import tempfile
import unittest
from pathlib import Path

from PyQt5.QtCore import QMutex

from serial_GUI import SerialTool


class _Signals:
    class _Progress:
        def emit(self, _message):
            pass

    progress = _Progress()


class _PartialTransport:
    is_open = True

    def __init__(self, result):
        self.result = result

    def write(self, _data):
        return self.result

    def flush(self):
        pass


class _FileSender:
    def __init__(self, transport):
        self.transport = transport
        self.serial_mutex = QMutex()
        self.stop_file_send = False


class FileSendingTests(unittest.TestCase):
    def _send(self, transport, payload=b"abcdef"):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "payload.bin"
            path.write_bytes(payload)
            sender = _FileSender(transport)
            return SerialTool._send_file_worker(sender, _Signals(), str(path), len(payload))

    def test部分写入不会虚报文件发送成功(self):
        success, message = self._send(_PartialTransport(2))

        self.assertFalse(success)
        self.assertIn("部分写入", message)

    def test连接关闭不会虚报文件发送成功(self):
        transport = _PartialTransport(0)
        transport.is_open = False

        success, message = self._send(transport)

        self.assertFalse(success)
        self.assertIn("连接已关闭", message)


if __name__ == "__main__":
    unittest.main()
