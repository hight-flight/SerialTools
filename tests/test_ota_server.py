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

        write_at = source.index("self.main_window.transport.write")
        self.assertLess(source.index("self._start_http_tracking"), write_at)
        self.assertLess(source.index("self._start_serial_tracking"), write_at)

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

        with mock.patch("ota_center.QMessageBox.warning"):
            OTAControlCenter._on_ota_timeout(controller)

        self.assertEqual(state["value"], "unconfirmed")
        self.assertIn("未确认", state["message"])


if __name__ == "__main__":
    unittest.main()
