import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QMutex
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QComboBox, QWidget

from auto_reply import AutoReplyDialog
from data_viewer import JsonCaptureThread
from serial_GUI import SerialTool


class _ProgressSignal:
    def __init__(self):
        self.messages = []

    def emit(self, message):
        self.messages.append(message)


class _RecordingTransport:
    is_open = True

    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)


class _AutoReplyParent(QWidget):
    def __init__(self):
        super().__init__()
        self.transport = _RecordingTransport()
        self.serial_mutex = QMutex()
        self.combo_encoding = QComboBox()
        self.combo_encoding.addItem("UTF-8")

    def append_text(self, _text):
        pass


class RuntimeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test接收线程必须在获取传输锁之前停止(self):
        source = inspect.getsource(SerialTool.cleanup_resources)
        self.assertLess(source.index("read_thread.stop"), source.index("with QMutexLocker"))

    def test日志备份worker接受signals并完成备份(self):
        self.assertIn("signals", inspect.signature(SerialTool.backup_current_file).parameters)
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "serial_data.txt"
            log_path.write_text("payload", encoding="utf-8")
            progress = _ProgressSignal()
            owner = SimpleNamespace(log_file_path=str(log_path), append_text=lambda _text: None)

            SerialTool.backup_current_file(owner, SimpleNamespace(progress=progress))

            self.assertEqual(Path(str(log_path) + ".bak").read_text(encoding="utf-8"), "payload")
            self.assertTrue(progress.messages)

    def test创建日志文件前执行保留数量清理(self):
        source = inspect.getsource(SerialTool.create_new_log_file)
        self.assertIn("self.rollover_log_files()", source)

    def test日志轮转同时删除对应备份(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_log = Path(temp_dir) / "serial_data_old.txt"
            new_log = Path(temp_dir) / "serial_data_new.txt"
            old_backup = Path(str(old_log) + ".bak")
            old_log.write_text("old", encoding="utf-8")
            old_backup.write_text("old backup", encoding="utf-8")
            new_log.write_text("new", encoding="utf-8")
            os.utime(old_log, (1, 1))
            os.utime(new_log, (2, 2))
            owner = SimpleNamespace(
                save_directory=temp_dir,
                max_log_files=2,
                append_text=lambda _text: None,
            )

            SerialTool.rollover_log_files(owner)

            self.assertFalse(old_log.exists())
            self.assertFalse(old_backup.exists())
            self.assertTrue(new_log.exists())

    def test自动回复拒绝字段类型错误的规则(self):
        invalid_cases = [
            {"trigger": "A", "response": "B", "max_count": "many"},
            {"trigger": "A", "response": "B", "enabled": "yes"},
            {"trigger": "A", "response": "B", "match_mode": "未知模式"},
            {"trigger": "", "response": "B"},
            {"trigger": "A", "response": "   "},
        ]
        for rule in invalid_cases:
            with self.subTest(rule=rule):
                valid, _message = AutoReplyDialog._validate_rules_data(None, [rule])
                self.assertFalse(valid)

    def test自动回复定时器触发后释放(self):
        parent = _AutoReplyParent()
        dialog = AutoReplyDialog(parent)
        self.addCleanup(parent.close)
        self.addCleanup(dialog.close)
        dialog.spin_delay.setValue(1)
        rule = {"response_format": "文本", "response": "OK", "newline": False}

        dialog._send_response(rule)
        self.assertEqual(len(getattr(dialog, "_response_timers", [])), 1)
        timer = next(iter(dialog._response_timers))
        timer.stop()
        timer.timeout.emit()

        self.assertEqual(len(dialog._response_timers), 0)
        self.assertEqual(parent.transport.writes, [b"OK"])

    def test自动回复保留缓冲区末尾以支持跨边界匹配(self):
        parent = _AutoReplyParent()
        dialog = AutoReplyDialog(parent)
        self.addCleanup(parent.close)
        self.addCleanup(dialog.close)
        dialog.spin_delay.setValue(1)
        dialog._rules = [{
            "trigger": "ABC", "match_mode": "文本包含", "response": "OK",
            "response_format": "文本", "enabled": True, "max_count": 0,
            "newline": False, "count": 0,
        }]

        with mock.patch.object(
            dialog,
            "_send_response",
            side_effect=lambda _rule: parent.transport.write(b"OK"),
        ):
            dialog._process_receive_data(b"x" * 4096 + b"A")
            dialog._process_receive_data(b"BC")

        self.assertEqual(parent.transport.writes, [b"OK"])

    def test解析队列超过容量时丢弃最旧数据块(self):
        capture = JsonCaptureThread()

        with mock.patch("data_viewer.MAX_CAPTURE_QUEUE_BYTES", 4, create=True):
            capture.enqueue_data(b"aaa")
            capture.enqueue_data(b"bbb")

        self.assertEqual(list(capture._queue), [b"bbb"])


if __name__ == "__main__":
    unittest.main()
