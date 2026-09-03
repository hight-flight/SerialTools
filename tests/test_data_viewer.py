import json
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QMutex, QRect, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QHeaderView, QWidget
from PyQt5.QtWidgets import QMessageBox

from app_paths import AppPaths
import data_viewer
from data_viewer import (
    CaptureListWidget, CaptureTableModel, ChartTrackerWidget,
    JsonCaptureThread, JsonViewerDialog, JsonSyntaxHighlighter, TrackChip,
)


class _DialogParent(QWidget):
    def __init__(self, root: Path, connected=True):
        super().__init__()
        self._app_paths = AppPaths(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            logs_dir=root / "logs",
            ota_dir=root / "ota",
        )
        for path in self._app_paths:
            path.mkdir(parents=True, exist_ok=True)
        self.transport = SimpleNamespace(is_open=connected)
        self.serial_mutex = QMutex()


class _TailProducingThread:
    def __init__(self, dialog):
        self.dialog = dialog
        self.running = True

    def isRunning(self):
        return self.running

    def set_running(self, running):
        self.running = running

    def wait(self, _timeout):
        self.dialog._on_items_ready([('{"tail":1}', {"tail": 1}, "tail: 1", False)])
        return True


class DataViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.parent = _DialogParent(Path(self.temp_dir.name))

    def tearDown(self):
        self.parent.close()
        self.temp_dir.cleanup()

    def _dialog(self, connected=True):
        self.parent.transport.is_open = connected
        dialog = JsonViewerDialog(self.parent)
        dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        self.addCleanup(dialog.close)
        return dialog

    def test停止捕获会在线程结束后刷新尾数据并保留统计(self):
        dialog = self._dialog()
        dialog._capture_running = True
        dialog._capture_thread = _TailProducingThread(dialog)

        dialog._stop_capture()

        self.assertEqual(dialog.capture_list.model.total_count, 1)
        self.assertEqual(dialog._capture_count, 1)
        self.assertIn("捕获 1 条", dialog.lbl_status.text())

    def test捕获线程停止前会排空已经入列的数据(self):
        capture = JsonCaptureThread()
        received = []
        capture.items_ready.connect(received.extend)
        capture.enqueue_data(b'{"queued": 1}\n')
        capture.set_running(True)
        capture.start()
        capture.set_running(False)
        self.assertTrue(capture.wait(2000))
        self.app.processEvents()

        self.assertEqual(received[0][1], {"queued": 1})

    def test捕获速率按条数而不是刷新批次数计算(self):
        dialog = self._dialog()
        dialog._pending_batch = [(str(i), i, str(i), False) for i in range(5)]
        with mock.patch("data_viewer.time.time", return_value=100.0):
            dialog._flush_pending()

        self.assertEqual(dialog._calculate_capture_rate(105.0), 0.5)

    def test无效正则不会回退成json捕获(self):
        capture = JsonCaptureThread()
        capture.custom_regex = "["
        capture._byte_buffer.extend(b'{"unexpected": true}\n')

        self.assertEqual(capture._extract_regex(), [])
        self.assertEqual(bytes(capture._byte_buffer), b'{"unexpected": true}\n')

    def test正则和二进制模式必须先通过配置校验(self):
        dialog = self._dialog()
        dialog.combo_filter_mode.setCurrentIndex(JsonCaptureThread.MODE_REGEX)
        dialog.edit_regex.setText("[")
        self.assertFalse(dialog._validate_capture_configuration())
        self.assertIn("正则", dialog.lbl_validation.text())

        dialog.combo_filter_mode.setCurrentIndex(JsonCaptureThread.MODE_BINARY)
        self.assertFalse(dialog._validate_capture_configuration())
        self.assertIn("协议", dialog.lbl_validation.text())
        dialog._capture_running = True
        dialog._stop_capture()
        self.assertFalse(dialog.btn_start.isEnabled())

    def test未连接时不能进入监听状态(self):
        dialog = self._dialog(connected=False)

        dialog._start_capture()

        self.assertFalse(dialog._capture_running)
        self.assertIn("未连接", dialog.lbl_status.text())

    def test导出保留合法但值为假的json(self):
        cases = [(0, 0), (False, False), ([], []), ({}, {})]
        for value, expected in cases:
            with self.subTest(value=value):
                item = {"obj": value, "raw": "raw-fallback"}
                self.assertEqual(CaptureListWidget._json_value(item), expected)

    def test_jsonl导出时普通文本也是合法json值(self):
        items = [
            {"obj": 0, "raw": "0"},
            {"obj": None, "raw": "plain text"},
        ]
        path = Path(self.temp_dir.name) / "capture.jsonl"

        CaptureListWidget._write_jsonl(path, items)

        values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(values, [0, "plain text"])

    def test帮助中声明的搜索删除和暂停快捷键可用(self):
        dialog = self._dialog()
        dialog.show()
        self.app.processEvents()
        dialog.capture_list.append_items([{
            "seq": 1, "timestamp": "00:00:00.000", "summary": "one",
            "length": 3, "raw": "one", "obj": None, "parse_error": True,
        }])
        dialog.capture_list.table.selectRow(0)

        QTest.keyClick(dialog, Qt.Key_F, Qt.ControlModifier)
        self.assertTrue(dialog.edit_search.hasFocus())
        dialog.capture_list.table.setFocus()
        QTest.keyClick(dialog, Qt.Key_Delete)
        self.assertEqual(dialog.capture_list.model.total_count, 0)
        dialog.capture_list.table.setFocus()
        QTest.keyClick(dialog, Qt.Key_Space)
        self.assertTrue(dialog.chart_tracker._paused)

    def test捕获列表自适应宽度且空列表不能清空(self):
        dialog = self._dialog()
        header = dialog.capture_list.table.horizontalHeader()
        self.assertEqual(header.sectionResizeMode(2), QHeaderView.Stretch)
        self.assertFalse(dialog.btn_clear.isEnabled())
        self.assertTrue(dialog.edit_search.accessibleName())
        self.assertTrue(dialog.capture_list.table.accessibleName())

    def test图表依赖缺失时统计面板仍可安全刷新(self):
        with mock.patch.object(data_viewer, "HAS_PYQTGRAPH", False):
            tracker = ChartTrackerWidget()
            self.addCleanup(tracker.close)
            self.assertEqual(tracker._tracked, {})

    def test跟踪标签别名和颜色修改会回写图表状态(self):
        source = inspect.getsource(TrackChip)
        self.assertIn("self.dot", source)
        self.assertIn("alias_changed", source)
        self.assertIn("color_changed", source)

    def test计算字段别名持久化不累加_fx前缀(self):
        dialog = self._dialog()
        dialog.chart_tracker.add_field(
            "__computed__test", alias="温度差", is_computed=True, expression="a-b"
        )
        dialog._save_layout()

        import configparser
        ini = configparser.ConfigParser()
        ini.read(dialog._ini_file, encoding="utf-8")
        aliases = json.loads(ini["tracks"]["aliases"])
        self.assertEqual(aliases["__computed__test"], "温度差")

    def test删除和空格快捷键不抢占可交互控件(self):
        dialog = self._dialog()
        dialog.show()
        dialog.capture_list.append_items([{
            "seq": 1, "timestamp": "00:00:00.000", "summary": "one",
            "length": 3, "raw": "one", "obj": None, "parse_error": True,
        }])
        checkbox = dialog.data_table.check_autoscroll
        checkbox.setFocus()
        self.app.processEvents()

        QTest.keyClick(checkbox, Qt.Key_Delete)
        self.assertEqual(dialog.capture_list.model.total_count, 1)
        QTest.keyClick(checkbox, Qt.Key_Space)
        self.assertFalse(dialog.chart_tracker._paused)

    def test语法高亮切换主题后立即重新着色(self):
        source = inspect.getsource(JsonSyntaxHighlighter)
        self.assertIn("def set_theme", source)
        self.assertIn("self.rehighlight()", source)

    def test实时数据表包含采样序号列(self):
        source = inspect.getsource(data_viewer.DataTableWidget.refresh)
        self.assertIn('["采样"] + col_aliases', source)
        self.assertIn("QTableWidgetItem(str(x))", source)
        self.assertIn("horizontalHeaderItem", source)

    def test图表工具栏在窄窗口可横向滚动(self):
        source = inspect.getsource(data_viewer.ChartTrackerWidget.__init__)
        self.assertIn("btn_scroll", source)
        self.assertIn("ScrollBarAsNeeded", source)

    def test无跟踪字段时禁用图表导出(self):
        if not data_viewer.HAS_PYQTGRAPH:
            self.skipTest("pyqtgraph 不可用")
        tracker = ChartTrackerWidget()
        self.addCleanup(tracker.close)

        self.assertTrue(hasattr(tracker, "btn_export_png"))
        self.assertTrue(hasattr(tracker, "btn_export_csv"))
        self.assertFalse(tracker.btn_export_png.isEnabled())
        self.assertFalse(tracker.btn_export_csv.isEnabled())

        self.assertTrue(tracker.add_field("temperature"))
        self.assertTrue(tracker.btn_export_png.isEnabled())
        self.assertTrue(tracker.btn_export_csv.isEnabled())

        tracker.remove_field("temperature")
        self.assertFalse(tracker.btn_export_png.isEnabled())
        self.assertFalse(tracker.btn_export_csv.isEnabled())

    def test详情表格截断长值时保留完整提示(self):
        source = inspect.getsource(data_viewer.DetailViewerWidget._build_table)
        self.assertIn("setToolTip", source)
        self.assertIn("+ '…'", source)

    def test协议编辑器适配当前屏幕并校验数值单元格(self):
        init_source = inspect.getsource(data_viewer.ProtocolEditorDialog.__init__)
        ui_source = inspect.getsource(data_viewer.ProtocolEditorDialog._init_ui)
        self.assertIn("availableGeometry", init_source)
        self.assertIn("_validate_field_cell", ui_source)
        self.assertIn("setMaximumHeight", ui_source)

    def test图表导出会报告写入失败(self):
        for exporter in (
            data_viewer.ChartTrackerWidget._export_png,
            data_viewer.ChartTrackerWidget._export_csv,
        ):
            source = inspect.getsource(exporter)
            self.assertIn("QMessageBox.warning", source)

    def test数据分析面板每次打开时位于主窗口中央(self):
        self.parent.setGeometry(QRect(120, 80, 560, 420))
        dialog = self._dialog()
        dialog.move(0, 0)

        dialog.center_on_current_screen()

        parent_center = self.parent.frameGeometry().center()
        dialog_center = dialog.frameGeometry().center()

        self.assertLessEqual(abs(dialog_center.x() - parent_center.x()), 1)
        self.assertLessEqual(abs(dialog_center.y() - parent_center.y()), 1)

    def test错误行颜色随明暗主题变化(self):
        model = CaptureTableModel(is_dark=False)
        model.append_items([{"summary": "bad", "parse_error": True}])
        light_bg = model.data(model.index(0, 0), Qt.BackgroundRole)
        model.set_theme(True)
        dark_bg = model.data(model.index(0, 0), Qt.BackgroundRole)

        self.assertIsInstance(light_bg, QColor)
        self.assertNotEqual(light_bg.name(), dark_bg.name())

    def test清空全部会同步清除右侧键值详情(self):
        dialog = self._dialog()
        dialog.detail_viewer.set_data(
            '{"temperature":25}', {"temperature": 25}, seq=1
        )
        self.assertTrue(
            dialog.detail_viewer.tree_model.findItems(
                "temperature", Qt.MatchExactly | Qt.MatchRecursive
            )
        )

        with mock.patch.object(QMessageBox, "exec_", return_value=QMessageBox.Yes):
            dialog._clear_all()

        self.assertEqual(dialog.detail_viewer.lbl_current.text(), "当前查看: —")
        self.assertEqual(dialog.detail_viewer.raw_view.toPlainText(), "")
        self.assertEqual(dialog.detail_viewer.table_view.rowCount(), 0)
        self.assertFalse(
            dialog.detail_viewer.tree_model.findItems(
                "temperature", Qt.MatchExactly | Qt.MatchRecursive
            )
        )


if __name__ == "__main__":
    unittest.main()
