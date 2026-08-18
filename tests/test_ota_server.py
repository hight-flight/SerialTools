import inspect
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from ota_center import OTAControlCenter, OTARequestHandler


class OTAServerTests(unittest.TestCase):
    def tearDown(self):
        OTARequestHandler.setup_progress("", 0)

    def test_http服务只允许访问当前固件(self):
        OTARequestHandler.setup_progress("firmware.bin", 128)

        self.assertTrue(OTARequestHandler.is_allowed_path("/firmware.bin"))
        self.assertFalse(OTARequestHandler.is_allowed_path("/firmware%20name.bin"))
        self.assertFalse(OTARequestHandler.is_allowed_path("/"))
        self.assertFalse(OTARequestHandler.is_allowed_path("/other.bin"))

    def test重复下载不会把发送字节重复累计(self):
        OTARequestHandler.setup_progress("firmware.bin", 10)
        handler = SimpleNamespace(
            path="/firmware.bin",
            translate_path=lambda _path: "firmware.bin",
        )

        OTARequestHandler.copyfile(handler, io.BytesIO(b"123456"), io.BytesIO())
        OTARequestHandler.copyfile(handler, io.BytesIO(b"123456"), io.BytesIO())

        sent, total, _filename = OTARequestHandler.get_progress()
        self.assertEqual(total, 10)
        self.assertEqual(sent, 6)

    def test发送ota命令前已经启用所有进度跟踪(self):
        source = inspect.getsource(OTAControlCenter._start_ota)

        send_at = source.index("self._send_ota_command(cmd_bytes")
        self.assertLess(source.index("self._start_http_tracking"), send_at)
        self.assertLess(source.index("self._start_serial_tracking"), send_at)

    def test开始升级按钮始终可以点击(self):
        class _Widget:
            def __init__(self):
                self.enabled = None
                self.label = None
                self.tooltip = None
                self.visible = None

            def setEnabled(self, value):
                self.enabled = value

            def setToolTip(self, value):
                self.tooltip = value

            def setText(self, value):
                self.label = value

            def setVisible(self, value):
                self.visible = value

        button = _Widget()
        precondition_label = _Widget()
        controller = SimpleNamespace(
            _btn_start_ota=button,
            _precondition_label=precondition_label,
            _start_button_block_reason=lambda: "请先选择固件文件",
            _ota_in_progress=False,
            _last_ota_command=b"",
        )

        OTAControlCenter._update_start_button_state(controller)

        self.assertTrue(button.enabled)
        self.assertEqual(button.label, "▶ 开始升级")
        self.assertEqual(button.tooltip, "请先选择固件文件")
        self.assertTrue(precondition_label.visible)

        controller._ota_in_progress = True
        OTAControlCenter._update_start_button_state(controller)
        self.assertEqual(button.label, "… 正在准备")

        controller._last_ota_command = b"AT+OTA"
        OTAControlCenter._update_start_button_state(controller)
        self.assertEqual(button.label, "↻ 重新下发")

    def test_ota状态提示不会被长异常撑宽(self):
        init_source = inspect.getsource(OTAControlCenter.init_ui)
        start_source = inspect.getsource(OTAControlCenter._start_ota)

        self.assertIn("self._state_label.setWordWrap(True)", init_source)
        self.assertIn("self._state_label.setMinimumWidth(0)", init_source)
        self.assertIn(
            'self._set_state(self.STATE_FAILED, "OTA 流程失败，请查看日志错误详情")',
            start_source,
        )
        self.assertNotIn(
            'self._set_state(self.STATE_FAILED, f"错误: {e}")',
            start_source,
        )
        self.assertNotIn(
            'QMessageBox.critical(self, "OTA 失败", str(e))',
            start_source,
        )

    def test升级进行中不再作为重复下发的阻断原因(self):
        controller = SimpleNamespace(
            _ota_in_progress=True,
            firmware_path="firmware.bin",
            main_window=SimpleNamespace(
                transport=SimpleNamespace(is_open=True),
            ),
            _ip_combo=SimpleNamespace(currentText=lambda: "192.168.1.2"),
        )

        with mock.patch("ota_center.os.path.isfile", return_value=True):
            reason = OTAControlCenter._start_button_block_reason(controller)

        self.assertEqual(reason, "")

    def test升级进行中再次点击只走重发分支(self):
        resend = mock.Mock()
        controller = SimpleNamespace(
            _ota_in_progress=True,
            _last_ota_command=b"AT+OTA",
            _resend_ota_command=resend,
        )

        OTAControlCenter._start_ota(controller)

        resend.assert_called_once_with()

    def test指令准备期间重复点击不会启动第二套流程(self):
        logs = []
        controller = SimpleNamespace(
            _ota_in_progress=True,
            _last_ota_command=b"",
            _log=logs.append,
        )

        OTAControlCenter._start_ota(controller)

        self.assertTrue(any("正在准备" in message for message in logs))

    def test重发只写缓存指令且不清空串口缓冲区(self):
        transport = mock.Mock(is_open=True)
        transport.write.return_value = len(b"AT+OTA")
        timeout_timer = mock.Mock()
        logs = []
        controller = SimpleNamespace(
            _last_ota_command=b"AT+OTA",
            _last_ota_send_at=0.0,
            _ota_send_count=1,
            main_window=SimpleNamespace(
                transport=transport,
                serial_mutex=object(),
                tx_bytes=0,
                label_tx_bytes=SimpleNamespace(setText=lambda _text: None),
                append_text=lambda _text: None,
            ),
            _timeout_timer=timeout_timer,
            _timeout_spin=SimpleNamespace(value=lambda: 30),
            _log=logs.append,
            _set_state=lambda _state, _message: None,
            STATE_DOWNLOADING="downloading",
            _update_start_button_state=lambda: None,
        )
        mutex_locker = mock.MagicMock()
        mutex_locker.return_value.__enter__.return_value = None

        with mock.patch("ota_center.QMutexLocker", mutex_locker), \
                mock.patch("ota_center.time.monotonic", return_value=10.0):
            OTAControlCenter._resend_ota_command(controller)

        transport.write.assert_called_once_with(b"AT+OTA")
        transport.reset_input_buffer.assert_not_called()
        transport.reset_output_buffer.assert_not_called()
        timeout_timer.start.assert_called_once_with(30000)
        self.assertEqual(controller._ota_send_count, 2)
        self.assertTrue(any("第 2 次" in message for message in logs))

    def test短时间双击不会重复下发(self):
        transport = mock.Mock(is_open=True)
        controller = SimpleNamespace(
            _last_ota_command=b"AT+OTA",
            _last_ota_send_at=10.0,
            _ota_send_count=1,
            main_window=SimpleNamespace(transport=transport),
            _log=lambda _message: None,
        )

        with mock.patch("ota_center.time.monotonic", return_value=10.2):
            OTAControlCenter._resend_ota_command(controller)

        transport.write.assert_not_called()

    def test重复下发不会重复绑定串口回调(self):
        signal = mock.Mock()
        controller = SimpleNamespace(
            _serial_data_connected=False,
            main_window=SimpleNamespace(
                read_thread=SimpleNamespace(receive_data_signal=signal),
            ),
            _on_serial_progress=lambda _data: None,
        )

        OTAControlCenter._start_serial_tracking(controller)
        OTAControlCenter._start_serial_tracking(controller)

        signal.connect.assert_called_once_with(controller._on_serial_progress)

    def test设备未确认时不能把ota标记为成功(self):
        class _Timer:
            def stop(self):
                pass

        class _ProgressBar:
            def __init__(self):
                self.value = None

            def setValue(self, value):
                self.value = value

        state = {}
        controller = SimpleNamespace(
            STATE_SUCCESS="success",
            STATE_FAILED="failed",
            STATE_UNCONFIRMED="unconfirmed",
            _progress_mode="http",
            _progress_poll_timer=_Timer(),
            _progress_bar=_ProgressBar(),
            _log=lambda message: state.setdefault("logs", []).append(message),
            _set_state=lambda value, message: state.update(value=value, message=message),
            _on_ota_finished=lambda: state.update(finished=True),
            _disconnect_serial_signal=lambda: None,
        )
        OTARequestHandler.setup_progress("firmware.bin", 10)
        with OTARequestHandler._lock:
            OTARequestHandler._bytes_sent = 10

        with mock.patch("ota_center.QMessageBox.warning") as warning:
            OTAControlCenter._on_ota_timeout(controller)

        self.assertEqual(state["value"], "unconfirmed")
        self.assertIn("未确认", state["message"])
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
