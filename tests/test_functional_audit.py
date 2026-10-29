import json
import select
import socket
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PyQt5.QtWidgets import QMessageBox
import serial

from serial_GUI import SerialTool
from transport import TransportWrapper
from tests import test_window_placement


ORIGINAL_LOAD_CONFIG = SerialTool.load_config


class NetworkAuditTests(unittest.TestCase):
    def test_udp按接收线程读取方式完整接收报文(self):
        transport = TransportWrapper()
        transport.open_udp("127.0.0.1", 0, "127.0.0.1", 9)
        self.addCleanup(transport.close)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sender.close)
        payload = bytes(range(256)) * 24
        sender.sendto(payload, transport._socket.getsockname())
        self.assertTrue(select.select([transport._socket], [], [], 1)[0])
        self.assertEqual(transport.read(transport.in_waiting or 4096), payload)

    def test_tcp接收不逐字节读取(self):
        local, peer = socket.socketpair()
        transport = TransportWrapper()
        transport.mode = "tcp_client"
        transport._socket = local
        self.addCleanup(transport.close)
        self.addCleanup(peer.close)
        payload = b"abc" * 100
        peer.sendall(payload)
        self.assertTrue(select.select([local], [], [], 1)[0])
        self.assertEqual(transport.read(transport.in_waiting or 4096), payload)

    def test串口读取异常不能被吞掉(self):
        transport = TransportWrapper()
        transport._serial = mock.Mock()
        transport._serial.read.side_effect = serial.SerialException("设备已拔出")
        with self.assertRaisesRegex(serial.SerialException, "设备已拔出"):
            transport.read(10)


class FunctionalAuditTests(unittest.TestCase):
    setUpClass = classmethod(test_window_placement.WindowPlacementTests.setUpClass.__func__)
    setUp = test_window_placement.WindowPlacementTests.setUp
    close_tool = test_window_placement.WindowPlacementTests.close_tool

    def test中文跨接收数据块不乱码(self):
        for encoding in ("UTF-8", "GBK", "GB18030"):
            with self.subTest(encoding=encoding):
                self.tool.combo_encoding.setCurrentText(encoding)
                self.tool.check_hex_recv.setChecked(False)
                self.tool._pending_display_data.clear()
                self.tool.act_timestamp.setChecked(False)
                self.tool.text_recv.clear()
                payload = "温度正常".encode(encoding)
                for byte in payload:
                    self.tool.handle_receive_data(bytes([byte]))
                self.tool._flush_pending_display()
                self.assertEqual(self.tool.text_recv.toPlainText(), "温度正常")

    def test大数据分段路径也保留跨块中文(self):
        payload = "测试".encode("utf-8")
        self.tool.act_timestamp.setChecked(False)
        self.tool.text_recv.clear()
        self.tool._handle_receive_data_chunk(payload[:2], "")
        self.tool._handle_receive_data_chunk(payload[2:], "")
        self.tool._flush_pending_display()
        self.assertEqual(self.tool.text_recv.toPlainText(), "测试")

    def test切换显示模式编码及连接后不拼接旧半字符(self):
        switches = (
            lambda: (self.tool.check_hex_recv.setChecked(True),
                     self.tool.check_hex_recv.setChecked(False)),
            lambda: (self.tool.combo_encoding.setCurrentText("GBK"),
                     self.tool.combo_encoding.setCurrentText("UTF-8")),
            self.tool.toggle_connection,
        )
        self.tool.act_timestamp.setChecked(False)
        for switch in switches:
            self.tool.handle_receive_data("中".encode("utf-8")[:1])
            switch()
            self.tool.text_recv.clear()
            self.tool.handle_receive_data(b"OK")
            self.tool._flush_pending_display()
            self.assertEqual(self.tool.text_recv.toPlainText(), "OK")

    def test正常文件完整发送且不超过启动时大小(self):
        path = Path(self.tool._app_paths.data_dir) / "complete.bin"
        payload = b"abc" * 1000
        path.write_bytes(payload + b"later")
        writes = []
        self.tool.transport = SimpleNamespace(
            is_open=True, write=lambda data: writes.append(data) or len(data),
            flush=lambda: None, close=lambda: None,
        )
        signals = SimpleNamespace(progress=SimpleNamespace(emit=lambda _message: None))
        self.assertEqual(self.tool._send_file_worker(signals, str(path), len(payload)),
                         (True, len(payload)))
        self.assertEqual(b"".join(writes), payload)

    def test等锁期间断开时不发送已读取的数据块(self):
        path = Path(self.tool._app_paths.data_dir) / "cancel.bin"
        path.write_bytes(b"abc")
        writes = []
        self.tool.transport = SimpleNamespace(
            is_open=True, write=lambda data: writes.append(data) or len(data),
            flush=lambda: None, close=lambda: None,
        )
        signals = SimpleNamespace(progress=SimpleNamespace(emit=lambda _message: None))
        with mock.patch("serial_GUI.QMutexLocker") as locker:
            locker.return_value.__enter__.side_effect = lambda: setattr(self.tool, "stop_file_send", True)
            success, detail = self.tool._send_file_worker(signals, str(path), 3)
        self.assertFalse(success)
        self.assertIn("取消", detail)
        self.assertEqual(writes, [])

    def test恢复网络配置显示正确的连接与刷新按钮(self):
        for mode, index in (("udp", 1), ("tcp_client", 2), ("tcp_server", 3)):
            with self.subTest(mode=mode):
                Path(self.tool.config_file).write_text(json.dumps({
                    "connection_mode": mode,
                    "tcp_server_local_ip": "127.0.0.1",
                    "udp_local_ip": "127.0.0.1",
                }), encoding="utf-8")
                ORIGINAL_LOAD_CONFIG(self.tool)
                self.assertEqual(self.tool.stack_params.currentIndex(), index)
                self.assertFalse(self.tool.btn_switch.isHidden())
                self.assertTrue(self.tool.btn_switch_serial.isHidden())
                self.assertEqual(self.tool.btn_refresh.isHidden(), mode == "tcp_client")
                if mode == "tcp_server":
                    self.assertEqual(self.tool.btn_switch.text(), "侦听")
                    self.assertEqual(self.tool.edit_tcp_server_local_ip.currentText(), "127.0.0.1")

    def test接收错误后可以切换通信模式(self):
        self.tool.combo_mode.setEnabled(False)
        with mock.patch.object(QMessageBox, "critical"):
            self.tool.handle_read_error("连接已关闭")
        self.assertTrue(self.tool.combo_mode.isEnabled())

    def test断开连接同时取消文件发送(self):
        self.tool.stop_file_send = False
        self.tool.toggle_connection()
        self.assertTrue(self.tool.stop_file_send)

    def test文件在发送前被截短不能报告成功(self):
        file_path = Path(self.tool._app_paths.data_dir) / "payload.bin"
        file_path.write_bytes(b"abc")
        self.tool.transport = SimpleNamespace(
            is_open=True, write=lambda data: len(data), flush=lambda: None, close=lambda: None,
        )
        signals = SimpleNamespace(progress=SimpleNamespace(emit=lambda _message: None))
        success, detail = self.tool._send_file_worker(signals, str(file_path), 10)
        self.assertFalse(success)
        self.assertIn("文件", detail)


if __name__ == "__main__":
    unittest.main()
