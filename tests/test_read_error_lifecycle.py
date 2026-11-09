import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")

import serial
from PyQt5.QtCore import QObject, QMutex, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QMessageBox

from ota_center import OTAControlCenter
from serial_GUI import SerialTool
from transport import TransportReadThread
from tests import test_window_placement


class _BrokenTransport:
    def __init__(self):
        self.is_open = True
        self.close_count = 0

    @property
    def in_waiting(self):
        raise serial.SerialException("ClearCommError failed (模拟设备断开)")

    def close(self):
        self.close_count += 1
        self.is_open = False


class _ReadWindow(QObject):
    handle_read_error = SerialTool.handle_read_error

    def __init__(self):
        super().__init__()
        self.transport = _BrokenTransport()
        self.serial_mutex = QMutex()
        self.read_thread = None
        self.error_state = False
        self.messages = []
        self._reset_receive_decoder = mock.Mock()
        self._reset_auto_reply_runtime = mock.Mock()
        self.append_text = self.messages.append
        self._sync_button_text = mock.Mock()
        self._update_status_connection_text = mock.Mock()
        self._set_status = mock.Mock()
        for name in ("btn_switch", "btn_switch_serial", "combo_port", "combo_baud",
                     "btn_refresh", "btn_refresh_serial", "combo_mode", "status_baud"):
            setattr(self, name, mock.Mock())
        self.combo_baud.currentText.return_value = "115200"


class _Signals(QObject):
    error_signal = pyqtSignal(str)
    receive_data_signal = pyqtSignal(bytes)

    def isRunning(self):
        return False


class ReadErrorLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test缓冲区查询失败只上报一次并退出(self):
        thread = TransportReadThread(_BrokenTransport(), QMutex())
        errors = []
        thread.error_signal.connect(errors.append)
        sleeps = []

        def bounded_sleep(_milliseconds):
            # 旧实现会无限重试，限制迭代次数以安全地验证失败。
            sleeps.append(1)
            if len(sleeps) == 3:
                thread.set_running(False)

        with mock.patch.object(thread, "msleep", side_effect=bounded_sleep):
            thread.run()

        self.assertEqual(len(errors), 1)
        self.assertIn("ClearCommError failed", errors[0])
        self.assertFalse(thread.is_running())

    def test重复读取错误只提示一次(self):
        window = _ReadWindow()
        with mock.patch("serial_GUI.QMessageBox.critical") as critical:
            window.handle_read_error("读取失败")
            window.handle_read_error("重复读取失败")

        self.assertEqual(critical.call_count, 1)
        self.assertEqual(window.transport.close_count, 1)
        self.assertEqual(len(window.messages), 1)

    def test提示前完成界面更新且重入不再弹窗(self):
        window = _ReadWindow()
        observed = []

        def show_error(*_args):
            observed.append(window._update_status_connection_text.call_args)
            if len(observed) == 1:
                window.handle_read_error("弹窗事件循环中的延迟错误")

        with mock.patch("serial_GUI.QMessageBox.critical", side_effect=show_error):
            window.handle_read_error("读取失败")

        self.assertEqual(observed, [mock.call(False)])
        self.assertFalse(window.transport.is_open)

    def test旧线程的排队错误不能关闭新连接(self):
        window = _ReadWindow()
        old_thread = _Signals()
        new_thread = _Signals()
        window.read_thread = old_thread
        old_thread.error_signal.connect(window.handle_read_error, Qt.QueuedConnection)
        old_thread.error_signal.emit("旧连接错误")
        window.read_thread = new_thread
        with mock.patch("serial_GUI.QMessageBox.critical") as critical:
            self.app.processEvents()

        self.assertTrue(window.transport.is_open)
        self.assertFalse(window.error_state)
        critical.assert_not_called()

    def test重新连接后的新错误仍能处理(self):
        window = _ReadWindow()
        with mock.patch("serial_GUI.QMessageBox.critical") as critical:
            window.handle_read_error("第一次连接错误")
            window.transport = _BrokenTransport()
            window.error_state = False
            window.handle_read_error("第二次连接错误")
        self.assertEqual(critical.call_count, 2)
        self.assertFalse(window.transport.is_open)

    def testOTA屏蔽期间错误仍会清理连接但不弹窗(self):
        window = _ReadWindow()
        thread = _Signals()
        window.read_thread = thread
        logs = []
        controller = SimpleNamespace(main_window=window, _log=logs.append)
        window._ota_dialog = controller
        thread.error_signal.connect(window.handle_read_error)
        OTAControlCenter._suppress_serial_errors(controller, True)
        with mock.patch("serial_GUI.QMessageBox.critical") as critical:
            thread.error_signal.emit("ClearCommError failed")

        self.assertFalse(window.transport.is_open)
        self.assertTrue(window.error_state)
        self.assertTrue(any("ClearCommError failed" in line for line in logs))
        critical.assert_not_called()

    def testOTA反复恢复不增加错误回调(self):
        thread = _Signals()
        errors = []
        window = SimpleNamespace(read_thread=thread, handle_read_error=errors.append)
        controller = SimpleNamespace(main_window=window)
        thread.error_signal.connect(window.handle_read_error)
        for _ in range(3):
            OTAControlCenter._suppress_serial_errors(controller, True)
            OTAControlCenter._suppress_serial_errors(controller, False)
            OTAControlCenter._suppress_serial_errors(controller, False)
        thread.error_signal.emit("一次错误")
        self.assertEqual(errors, ["一次错误"])

    def test关闭OTA窗口恢复提示并结束本地跟踪(self):
        controller = SimpleNamespace(
            main_window=None, _ota_in_progress=True, _last_ota_command=b"ota",
            _serial_errors_suppressed=True, _save_settings=lambda: None,
            _progress_poll_timer=mock.Mock(), _timeout_timer=mock.Mock(),
            _fallback_timer=mock.Mock(), _disconnect_serial_signal=lambda: None,
            http_running=True, STATE_UNCONFIRMED="unconfirmed", _set_state=mock.Mock(),
        )
        controller._suppress_serial_errors = lambda value: OTAControlCenter._suppress_serial_errors(controller, value)
        OTAControlCenter.closeEvent(controller, mock.Mock())
        self.assertFalse(controller._serial_errors_suppressed)
        self.assertFalse(controller._ota_in_progress)
        self.assertEqual(controller._last_ota_command, b"")
        self.assertTrue(controller.http_running)

    def testOTA清理断开原线程而不是重连后的线程(self):
        old_thread, new_thread = _Signals(), _Signals()
        received = []
        controller = SimpleNamespace(
            main_window=SimpleNamespace(read_thread=old_thread),
            _serial_data_connected=False, _on_serial_progress=received.append,
        )
        OTAControlCenter._start_serial_tracking(controller)
        controller.main_window.read_thread = new_thread
        OTAControlCenter._disconnect_serial_signal(controller)
        old_thread.receive_data_signal.emit(b"ota: Progress:50%")
        self.assertEqual(received, [])


class ReadErrorIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(test_window_placement.WindowPlacementTests.setUpClass.__func__)
    setUp = test_window_placement.WindowPlacementTests.setUp
    close_tool = test_window_placement.WindowPlacementTests.close_tool

    def test固件复制重试期间关闭监控仍保留HTTP服务(self):
        with mock.patch.object(OTAControlCenter, "_get_local_ips", return_value=["127.0.0.1"]):
            dialog = OTAControlCenter(self.tool)
        self.tool._ota_dialog = dialog
        firmware = Path(self.tool._app_paths.data_dir) / "firmware.bin"
        firmware.write_bytes(b"firmware")
        dialog.firmware_path = str(firmware)
        dialog._ip_combo.addItem("127.0.0.1")
        dialog.http_running = True
        dialog._check_auto_stop.setChecked(True)
        self.tool.transport = _BrokenTransport()
        with mock.patch("ota_center.shutil.copy2", side_effect=PermissionError(13, "文件占用")), \
                mock.patch("ota_center.QApplication.processEvents", side_effect=dialog.close), \
                mock.patch("ota_center.QMessageBox.warning") as warning, \
                mock.patch.object(dialog, "_stop_http_service") as stop_http:
            dialog._start_ota()
        warning.assert_not_called()
        self.assertEqual(dialog.ota_state, dialog.STATE_UNCONFIRMED)
        stop_http.assert_not_called()
        self.assertTrue(dialog.http_running)
        self.assertFalse(dialog._ota_in_progress)

    def test关闭升级监控后状态与按钮不残留运行中(self):
        with mock.patch.object(OTAControlCenter, "_get_local_ips", return_value=["127.0.0.1"]):
            dialog = OTAControlCenter(self.tool)
        self.tool._ota_dialog = dialog
        dialog._ota_in_progress = True
        dialog._set_state(dialog.STATE_DOWNLOADING, "等待设备下载")
        dialog.close()
        self.assertEqual(dialog.ota_state, dialog.STATE_UNCONFIRMED)
        self.assertFalse(dialog._btn_stop_ota.isEnabled())

    def test真实错误弹窗一次点击即可关闭且界面已断开(self):
        self.tool.transport = _BrokenTransport()
        self.tool.btn_switch.setChecked(True)
        self.tool.combo_mode.setEnabled(False)
        observations = []
        timer = QTimer()

        def dismiss():
            box = self.app.activeModalWidget()
            if not isinstance(box, QMessageBox):
                return
            observations.append((self.tool.btn_switch.isChecked(), self.tool.combo_mode.isEnabled()))
            # 即使防重入将来回归，也只注入一次重复错误，让测试失败而不是无限弹窗。
            if len(observations) == 1:
                self.tool.handle_read_error("弹窗期间重复错误")
            box.button(QMessageBox.Ok).click()

        timer.timeout.connect(dismiss)
        timer.start(50)
        try:
            self.tool.handle_read_error("ClearCommError failed (模拟设备断开)")
        finally:
            timer.stop()
        self.assertEqual(observations, [(False, True)])
        self.assertIsNone(self.app.activeModalWidget())

    def testOTA重连后继续接收设备进度(self):
        with mock.patch.object(OTAControlCenter, "_get_local_ips", return_value=["127.0.0.1"]):
            dialog = OTAControlCenter(self.tool)
        self.tool._ota_dialog = dialog
        old_thread = _Signals()
        self.tool.read_thread = old_thread
        dialog._ota_in_progress = True
        dialog._start_serial_tracking()
        dialog._suppress_serial_errors(True)
        # 使用真实连接切换入口，传输采用空闲模拟串口，避免访问设备。
        transport = SimpleNamespace(
            is_open=False, in_waiting=0, close=lambda: None,
            open_serial=lambda **_kwargs: setattr(transport, "is_open", True),
        )
        self.tool.transport = transport
        self.tool.combo_port.addItem("模拟串口")
        self.tool.btn_switch.setChecked(True)
        with mock.patch("serial_GUI.QMessageBox.critical") as critical:
            self.tool.toggle_connection()
        critical.assert_not_called()
        self.tool.read_thread.receive_data_signal.emit(b"ota: Progress:75%")
        self.assertEqual(dialog._progress_bar.value(), 75)
        old_thread.receive_data_signal.emit(b"ota: Progress:25%")
        self.assertEqual(dialog._progress_bar.value(), 75)

    def testOTA实际接收线程异常保留下载跟踪且不弹窗(self):
        with mock.patch.object(OTAControlCenter, "_get_local_ips", return_value=["127.0.0.1"]):
            dialog = OTAControlCenter(self.tool)
        self.tool._ota_dialog = dialog
        self.tool.transport = _BrokenTransport()
        thread = TransportReadThread(self.tool.transport, self.tool.serial_mutex)
        self.tool.read_thread = thread
        thread.error_signal.connect(self.tool.handle_read_error)
        dialog._ota_in_progress = True
        dialog._suppress_serial_errors(True)
        dialog._start_serial_tracking()
        dialog._timeout_timer.start(10000)
        with mock.patch("serial_GUI.QMessageBox.critical") as critical:
            thread.start()
            self.assertTrue(thread.wait(1000))
            self.app.processEvents()
        self.assertFalse(self.tool.transport.is_open)
        self.assertTrue(self.tool.error_state)
        self.assertTrue(dialog._ota_in_progress)
        self.assertTrue(dialog._timeout_timer.isActive())
        critical.assert_not_called()


if __name__ == "__main__":
    unittest.main()
