import os
import inspect
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox, QAbstractItemView, QCheckBox

from app_paths import AppPaths
from serial_GUI import SerialTool
from theme import DARK_QSS, LIGHT_QSS

ORIGINAL_LOAD_CONFIG = SerialTool.load_config


class MultiSendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        paths = AppPaths(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            logs_dir=root / "logs",
            ota_dir=root / "ota",
        )
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)

        patches = (
            mock.patch("serial_GUI.resolve_app_paths", return_value=paths),
            mock.patch.object(SerialTool, "refresh_ports"),
            mock.patch.object(SerialTool, "load_config"),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.tool = SerialTool()

    def tearDown(self):
        thread = getattr(self.tool, "batch_thread", None)
        if thread is not None and hasattr(thread, "request_stop"):
            thread.request_stop()
        if thread is not None and hasattr(thread, "wait"):
            thread.wait(1000)
        self.app.processEvents()
        self.tool.close()
        self.tool.setAttribute(Qt.WA_DeleteOnClose, True)
        self.tool.deleteLater()
        for _ in range(3):
            self.app.processEvents()
        self.tool = None
        self.temp_dir.cleanup()

    def _dispose_thread(self, thread):
        if thread.isRunning():
            thread.request_stop()
            thread.wait(1000)
        thread.deleteLater()
        for _ in range(3):
            self.app.processEvents()

    def test单行发送保留主发送草稿且不启动普通重复发送(self):
        self.tool.transport = SimpleNamespace(
            is_open=True,
            write=lambda data: len(data),
            close=lambda: None,
        )
        self.tool.text_send.setPlainText("尚未发送的草稿")
        self.tool.check_repeat.setChecked(True)
        self.tool.table_multi_send.item(0, 1).setText("AT+CSQ")

        self.tool.send_multi_item(0)

        self.assertEqual(self.tool.text_send.toPlainText(), "尚未发送的草稿")
        self.assertTrue(self.tool.check_repeat.isChecked())
        self.assertFalse(self.tool.repeat_timer.isActive())

    def test浏览历史时单行发送后仍可返回原草稿(self):
        self.tool.transport = SimpleNamespace(
            is_open=True, write=lambda data: len(data), close=lambda: None,
        )
        self.tool.send_history.appendleft("历史指令")
        self.tool.text_send.setPlainText("原始草稿")
        self.tool._navigate_history(-1)
        self.tool.table_multi_send.item(0, 1).setText("AT+CSQ")

        self.tool.send_multi_item(0)
        self.tool._navigate_history(1)

        self.assertEqual(self.tool.text_send.toPlainText(), "原始草稿")

    def test浏览历史时批量发送后仍可返回原草稿(self):
        from serial_GUI import MultiSendPayload

        self.tool.transport = SimpleNamespace(
            is_open=True, write=lambda data: len(data), close=lambda: None,
        )
        self.tool.send_history.appendleft("历史指令")
        self.tool.text_send.setPlainText("原始草稿")
        self.tool._navigate_history(-1)
        fake_thread = SimpleNamespace(
            is_stop_requested=lambda: False,
            report_send_result=mock.Mock(),
        )
        self.tool.batch_thread = fake_thread

        self.tool.send_multi_payload(
            MultiSendPayload("AT+CSQ", False, 0, 1, "测试", 0)
        )
        self.tool._navigate_history(1)

        self.assertEqual(self.tool.text_send.toPlainText(), "原始草稿")
        fake_thread.report_send_result.assert_called_once_with(True)
        self.tool.batch_thread = None

    def test批量发送使用不可变内容快照而不是行号(self):
        self.tool.table_multi_send.item(0, 1).setText("AT+ONE")
        self.tool.table_multi_send.item(0, 5).setText("1")
        self.tool.add_multi_item()
        self.tool.table_multi_send.item(1, 1).setText("AT+TWO")
        self.tool.table_multi_send.item(1, 5).setText("2")

        items, errors = self.tool._collect_multi_send_snapshot()
        self.tool.table_multi_send.removeRow(0)
        self.tool.table_multi_send.item(0, 1).setText("AT+CHANGED")

        self.assertEqual(errors, [])
        self.assertIsInstance(items, tuple)
        self.assertEqual([item.text for item in items], ["AT+ONE", "AT+TWO"])
        self.assertEqual([item.order for item in items], [1, 2])

    def test批量快照拒绝空指令与无效顺序并返回单元格错误(self):
        self.tool.table_multi_send.item(0, 1).setText("")
        self.tool.table_multi_send.item(0, 5).setText("1")
        self.tool.add_multi_item()
        self.tool.table_multi_send.item(1, 1).setText("AT+CSQ")
        self.tool.table_multi_send.item(1, 5).setText("0")

        items, errors = self.tool._collect_multi_send_snapshot()

        self.assertEqual(items, ())
        self.assertIn((0, 1, "字符串不能为空"), errors)
        self.assertIn((1, 5, "顺序必须是大于 0 的整数"), errors)

    def test批量发送使用明确的开始停止按钮并在完成后复位(self):
        self.assertEqual(self.tool.btn_batch_send.text(), "开始批量发送")

        self.tool._set_batch_running(True)

        self.assertEqual(self.tool.btn_batch_send.text(), "停止批量发送")
        self.assertFalse(self.tool.table_multi_send.isEnabled())
        self.assertFalse(self.tool.btn_load_multi.isEnabled())

        self.tool._finish_batch_send("完成")

        self.assertEqual(self.tool.btn_batch_send.text(), "开始批量发送")
        self.assertFalse(self.tool.btn_batch_send.isChecked())
        self.assertTrue(self.tool.table_multi_send.isEnabled())
        self.assertTrue(self.tool.btn_load_multi.isEnabled())
        self.assertIn("已完成", self.tool.label_batch_status.text())

    def test停止批量发送只发出中断请求而不等待线程(self):
        fake_thread = SimpleNamespace(
            isRunning=lambda: True,
            request_stop=mock.Mock(),
        )
        self.tool.batch_thread = fake_thread

        self.tool.stop_batch_send()

        fake_thread.request_stop.assert_called_once_with()
        self.tool.batch_thread = None

    def test批量线程按快照发送并报告正常完成(self):
        from serial_GUI import BatchSendThread, MultiSendPayload
        from PyQt5.QtCore import QEventLoop, QTimer

        item = MultiSendPayload("AT+CSQ", False, 0, 1, "信号质量", 0)
        sent = []
        results = []
        thread = BatchSendThread((item,), cycle_delay_ms=0, cycle_count=1)
        def acknowledge(item):
            sent.append(item)
            thread.report_send_result(True)

        thread.send_requested.connect(acknowledge)
        thread.batch_finished.connect(results.append)
        event_loop = QEventLoop()
        thread.batch_finished.connect(event_loop.quit)

        thread.start()
        QTimer.singleShot(1000, event_loop.quit)
        event_loop.exec_()
        self.assertTrue(thread.wait(1000))
        self.app.processEvents()

        self.assertEqual(sent, [item])
        self.assertEqual(results, ["完成"])
        self._dispose_thread(thread)

    def test批量真实写失败会停止并报告错误(self):
        import serial
        from PyQt5.QtCore import QEventLoop, QTimer

        self.tool.transport = SimpleNamespace(
            is_open=True,
            write=mock.Mock(side_effect=serial.SerialException("设备断开")),
            close=lambda: None,
        )
        self.tool.table_multi_send.item(0, 1).setText("AT+CSQ")
        delay_widget = self.tool.table_multi_send.cellWidget(0, 4)
        delay_widget.layout().itemAt(0).widget().setValue(100)
        results = []
        event_loop = QEventLoop()

        with mock.patch("serial_GUI.QMessageBox.warning"):
            self.tool.batch_send()
            self.tool.batch_thread.batch_finished.connect(results.append)
            self.tool.batch_thread.batch_finished.connect(event_loop.quit)
            QTimer.singleShot(1500, event_loop.quit)
            event_loop.exec_()

        self.assertEqual(results, ["错误"])
        self.assertEqual(self.tool.label_batch_status.text(), "发送失败")
        self.assertFalse(self.tool.btn_batch_send.isChecked())

    def test批量线程的长延时可被立即中断(self):
        from serial_GUI import BatchSendThread, MultiSendPayload

        item = MultiSendPayload("AT+CSQ", False, 10000, 1, "信号质量", 0)
        thread = BatchSendThread((item,), cycle_delay_ms=10000, cycle_count=None)
        thread.start()
        time.sleep(0.05)

        started = time.monotonic()
        thread.request_stop()
        stopped = thread.wait(500)

        self.assertTrue(stopped)
        self.assertLess(time.monotonic() - started, 0.5)
        self._dispose_thread(thread)

    def test停止后忽略已经排队但尚未执行的发送请求(self):
        from serial_GUI import BatchSendThread, MultiSendPayload

        write = mock.Mock(return_value=6)
        self.tool.transport = SimpleNamespace(
            is_open=True, write=write, close=lambda: None,
        )
        item = MultiSendPayload("AT+CSQ", False, 1000, 1, "信号质量", 0)
        thread = BatchSendThread((item,), cycle_delay_ms=0, cycle_count=1, parent=self.tool)
        self.tool.batch_thread = thread
        thread.send_requested.connect(self.tool.send_multi_payload)

        thread.start()
        time.sleep(0.05)  # 让 send_requested 进入主线程事件队列，但暂不处理
        thread.request_stop()
        self.app.processEvents()
        self.assertTrue(thread.wait(1000))

        write.assert_not_called()
        self._dispose_thread(thread)
        self.tool.batch_thread = None

    def test批量发送线程接收的是内容快照(self):
        from serial_GUI import BatchSendThread

        self.tool.transport = SimpleNamespace(
            is_open=True,
            write=lambda data: len(data),
            close=lambda: None,
        )
        self.tool.table_multi_send.item(0, 1).setText("AT+ONE")
        self.tool.table_multi_send.item(0, 5).setText("1")
        self.tool.check_cycle_count.setChecked(True)
        self.tool.spin_cycle_count.setValue(1)

        self.tool.batch_send()
        thread = self.tool.batch_thread

        self.assertIsInstance(thread, BatchSendThread)
        self.assertEqual([item.text for item in thread.items], ["AT+ONE"])
        self._dispose_thread(thread)
        self.tool.batch_thread = None

    def test_csv解析报告坏行并读取批量设置(self):
        from serial_GUI import parse_multi_send_csv

        content = (
            "hex,string,button_text,delay,order,cycle_delay,limit_cycles,cycle_count\n"
            "false,AT+CSQ,信号质量,100,1,500,true,3\n"
            "true,AA55,握手,错误,2,500,true,3\n"
            "false,,空指令,100,3,500,true,3\n"
            "true,GG,坏HEX,100,4,500,true,3\n"
        )

        items, issues, settings = parse_multi_send_csv(content)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["string"], "AT+CSQ")
        self.assertEqual(issues, [
            "第 3 行：延时必须是 0–10000 的整数",
            "第 4 行：字符串不能为空",
            "第 5 行：HEX 格式无效",
        ])
        self.assertEqual(settings, {
            "cycle_delay": 500,
            "limit_cycles": True,
            "cycle_count": 3,
        })

    def test加载csv前拒绝覆盖时保留当前列表(self):
        csv_path = Path(self.temp_dir.name) / "commands.csv"
        csv_path.write_text(
            "hex,string,button_text,delay,order\nfalse,AT+NEW,新指令,100,1\n",
            encoding="utf-8",
        )
        self.tool.table_multi_send.item(0, 1).setText("AT+KEEP")

        with mock.patch(
            "serial_GUI.QFileDialog.getOpenFileName",
            return_value=(str(csv_path), "CSV Files (*.csv)"),
        ), mock.patch(
            "serial_GUI.QMessageBox.question",
            return_value=QMessageBox.No,
        ) as question:
            self.tool.load_multi_items()

        question.assert_called_once()
        self.assertEqual(self.tool.table_multi_send.item(0, 1).text(), "AT+KEEP")

    def test加载csv会确认覆盖非字符串字段和批量设置(self):
        csv_path = Path(self.temp_dir.name) / "commands.csv"
        csv_path.write_text(
            "hex,string,button_text,delay,order\nfalse,AT+NEW,新指令,100,1\n",
            encoding="utf-8",
        )
        self.tool.table_multi_send.item(0, 2).setText("仅修改备注")
        self.tool.spin_delay.setValue(250)

        with mock.patch(
            "serial_GUI.QFileDialog.getOpenFileName",
            return_value=(str(csv_path), "CSV Files (*.csv)"),
        ), mock.patch(
            "serial_GUI.QMessageBox.question",
            return_value=QMessageBox.No,
        ) as question:
            self.tool.load_multi_items()

        question.assert_called_once()
        self.assertEqual(self.tool.table_multi_send.item(0, 2).text(), "仅修改备注")
        self.assertEqual(self.tool.spin_delay.value(), 250)

    def test保存csv包含批量设置且大写扩展名不重复追加(self):
        import csv

        csv_path = Path(self.temp_dir.name) / "commands.CSV"
        self.tool.table_multi_send.item(0, 1).setText("AT+CSQ")
        self.tool.spin_delay.setValue(500)
        self.tool.check_cycle_count.setChecked(True)
        self.tool.spin_cycle_count.setValue(3)

        with mock.patch(
            "serial_GUI.QFileDialog.getSaveFileName",
            return_value=(str(csv_path), "CSV Files (*.csv)"),
        ):
            self.tool.save_multi_items()

        self.assertTrue(csv_path.exists())
        self.assertFalse(Path(f"{csv_path}.csv").exists())
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["cycle_delay"], "500")
        self.assertEqual(row["limit_cycles"], "True")
        self.assertEqual(row["cycle_count"], "3")

    def test主配置保存并恢复批量发送设置(self):
        import json

        self.tool.spin_delay.setValue(750)
        self.tool.check_cycle_count.setChecked(True)
        self.tool.spin_cycle_count.setValue(4)
        self.tool.save_config()

        with open(self.tool.config_file, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["multi_cycle_delay"], 750)
        self.assertTrue(saved["multi_limit_cycles"])
        self.assertEqual(saved["multi_cycle_count"], 4)

        saved.update({
            "multi_cycle_delay": 250,
            "multi_limit_cycles": False,
            "multi_cycle_count": 6,
        })
        with open(self.tool.config_file, "w", encoding="utf-8") as handle:
            json.dump(saved, handle)

        ORIGINAL_LOAD_CONFIG(self.tool)

        self.assertEqual(self.tool.spin_delay.value(), 250)
        self.assertFalse(self.tool.check_cycle_count.isChecked())
        self.assertEqual(self.tool.spin_cycle_count.value(), 6)

    def test表格将备注与发送操作分列且不会单击即编辑(self):
        headers = [
            self.tool.table_multi_send.horizontalHeaderItem(column).text()
            for column in range(self.tool.table_multi_send.columnCount())
        ]

        self.assertEqual(
            headers,
            ["HEX", "字符串", "名称/备注", "操作", "单条延时(ms)", "顺序"],
        )
        self.assertFalse(
            self.tool.table_multi_send.editTriggers()
            & QAbstractItemView.CurrentChanged
        )
        self.assertIsNotNone(self.tool.table_multi_send.item(0, 2))
        send_button = self.tool.table_multi_send.cellWidget(0, 3).layout().itemAt(0).widget()
        self.assertEqual(send_button.text(), "发送")
        self.assertIn("第 1 行", send_button.accessibleName())

    def test行内控件具有可访问名称且交互时选中所属行(self):
        self.tool.add_multi_item()
        hex_checkbox = self.tool.table_multi_send.cellWidget(1, 0).layout().itemAt(0).widget()
        delay_spin = self.tool.table_multi_send.cellWidget(1, 4).layout().itemAt(0).widget()

        self.assertEqual(hex_checkbox.accessibleName(), "第 2 行 HEX 发送")
        self.assertEqual(delay_spin.accessibleName(), "第 2 行单条延时")
        hex_checkbox.click()
        self.assertEqual(self.tool.table_multi_send.currentRow(), 1)

    def test删除选中指令不弹确认框(self):
        self.tool.add_multi_item()
        self.tool.table_multi_send.selectRow(1)

        with mock.patch("serial_GUI.QMessageBox.question") as question:
            self.tool.remove_multi_item()

        question.assert_not_called()
        self.assertEqual(self.tool.table_multi_send.rowCount(), 1)
        self.assertEqual(self.tool.label_batch_status.text(), "已删除 1 条指令")

    def test单行发送在未连接或内容为空时给出面板反馈(self):
        self.tool.transport = SimpleNamespace(is_open=False, close=lambda: None)
        self.tool.table_multi_send.item(0, 1).setText("AT+CSQ")

        self.tool.send_multi_item(0)

        self.assertEqual(self.tool.label_batch_status.text(), "未连接，未发送")

        self.tool.transport.is_open = True
        self.tool.table_multi_send.item(0, 1).setText("")
        self.tool.send_multi_item(0)

        self.assertEqual(self.tool.label_batch_status.text(), "第 1 行内容为空")

    def test批量校验拒绝无效hex内容(self):
        hex_checkbox = self.tool.table_multi_send.cellWidget(0, 0).layout().itemAt(0).widget()
        hex_checkbox.setChecked(True)
        self.tool.table_multi_send.item(0, 1).setText("GG")
        self.tool.table_multi_send.item(0, 5).setText("1")

        items, errors = self.tool._collect_multi_send_snapshot()

        self.assertEqual(items, ())
        self.assertIn((0, 1, "HEX 格式无效"), errors)

    def test批量发送默认限定为一轮以避免误触无限发送(self):
        self.assertTrue(self.tool.check_cycle_count.isChecked())
        self.assertTrue(self.tool.spin_cycle_count.isEnabled())
        self.assertEqual(self.tool.spin_cycle_count.value(), 1)

    def test主键盘导航顺序进入多字符串操作区(self):
        self.assertIs(
            self.tool.btn_toggle_multi_send.nextInFocusChain(),
            self.tool.btn_batch_send,
        )
        self.assertIs(
            self.tool.btn_batch_send.nextInFocusChain(),
            self.tool.spin_delay,
        )

    def test多字符串表格使用紧凑宽度(self):
        self.assertEqual(self.tool.table_multi_send.minimumWidth(), 360)

    def test主窗口默认宽度为1080并受屏幕可用宽度限制(self):
        available_width = QApplication.primaryScreen().availableGeometry().width()
        self.assertEqual(self.tool.width(), min(1080, available_width))

        self.tool.show()
        self.tool.toggle_multi_send()
        self.app.processEvents()
        left_width, right_width = self.tool.main_splitter.sizes()

        if available_width >= 1080:
            self.assertGreaterEqual(left_width, 680)
            self.assertGreaterEqual(right_width, 360)
            self.assertLessEqual(right_width, 390)
        else:
            # 小屏无法同时容纳两个理想宽度时，窗口不越界并保留右侧最小可用宽度。
            self.assertGreater(left_width, 0)
            self.assertGreaterEqual(right_width, int(available_width * 0.45))

    def test展开面板时窗口限制在当前副屏可用区域内(self):
        available = QRect(100, 50, 1024, 700)
        fake_screen = SimpleNamespace(availableGeometry=lambda: available)
        self.tool.setGeometry(900, 400, 1280, 900)

        with mock.patch.object(SerialTool, "screen", return_value=fake_screen):
            self.tool._fit_multi_send_to_current_screen()

        geometry = self.tool.geometry()
        self.assertLessEqual(geometry.width(), available.width())
        self.assertLessEqual(geometry.height(), available.height())
        self.assertGreaterEqual(geometry.left(), available.left())
        self.assertGreaterEqual(geometry.top(), available.top())
        self.assertLessEqual(geometry.right(), available.right())
        self.assertLessEqual(geometry.bottom(), available.bottom())

    def test展开面板时目标宽度为1080(self):
        available = QRect(0, 0, 1600, 900)
        fake_screen = SimpleNamespace(availableGeometry=lambda: available)
        self.tool.setGeometry(100, 50, 900, 800)

        with mock.patch.object(SerialTool, "screen", return_value=fake_screen):
            self.tool._fit_multi_send_to_current_screen()

        self.assertEqual(self.tool.width(), 1080)

    def test批量状态单独位于按钮下方并可完整显示(self):
        self.tool.show()
        self.tool.toggle_multi_send()
        self.app.processEvents()

        self.assertGreaterEqual(
            self.tool.label_batch_status.geometry().top()
            - self.tool.btn_help_multi.geometry().top(),
            24,
        )
        self.assertGreaterEqual(self.tool.label_batch_status.minimumWidth(), 160)

    def test_hex使用方框并在框内绘制对勾(self):
        checkbox = self.tool.table_multi_send.cellWidget(0, 0).layout().itemAt(0).widget()
        self.tool.table_multi_send.selectRow(0)

        self.assertGreaterEqual(checkbox.minimumWidth(), 24)
        self.assertGreaterEqual(checkbox.minimumHeight(), 24)

        checkbox.setChecked(True)

        self.assertEqual(checkbox.text(), "")
        self.assertIsNot(type(checkbox).paintEvent, QCheckBox.paintEvent)
        self.assertIn("QTableWidget QCheckBox::indicator", DARK_QSS)
        self.assertIn("width: 0px", DARK_QSS)
        self.assertIn("QTableWidget QCheckBox::indicator", LIGHT_QSS)
        self.assertNotIn("#FFB300", DARK_QSS)
        self.assertNotIn("#FF8F00", LIGHT_QSS)

    def test_hex空白状态的整个控件区域均可点击(self):
        checkbox = self.tool.table_multi_send.cellWidget(0, 0).layout().itemAt(0).widget()
        self.tool.show()
        self.app.processEvents()

        QTest.mouseClick(checkbox, Qt.LeftButton, pos=checkbox.rect().center())

        self.assertTrue(checkbox.isChecked())
        self.assertEqual(checkbox.text(), "")

    def test帮助按钮与加载按钮位于同一行(self):
        self.tool.show()
        self.tool.toggle_multi_send()
        self.app.processEvents()

        self.assertGreaterEqual(
            self.tool.check_cycle_count.geometry().top()
            - self.tool.btn_batch_send.geometry().top(),
            24,
        )
        self.assertEqual(
            self.tool.btn_help_multi.geometry().top(),
            self.tool.btn_load_multi.geometry().top(),
        )
        self.assertGreater(
            self.tool.btn_help_multi.geometry().left(),
            self.tool.btn_load_multi.geometry().left(),
        )

    def test暗色主题提高表格与交互控件的前景和边框对比度(self):
        self.assertIn("color: #E6EAF0", DARK_QSS)
        self.assertIn("border: 1px solid #4B5363", DARK_QSS)

    def test暗色主题下批量校验错误保持可读对比度(self):
        self.tool.current_theme = "dark"
        self.tool.table_multi_send.item(0, 1).setText("")

        self.tool._show_multi_send_errors([(0, 1, "字符串不能为空")])

        item = self.tool.table_multi_send.item(0, 1)
        self.assertEqual(item.background().color().name(), "#5c2b31")
        self.assertEqual(item.foreground().color().name(), "#ffcdd2")

    def test帮助文案与新的批量操作和表格结构一致(self):
        source = inspect.getsource(SerialTool.show_multi_send_help)
        self.assertIn("开始批量发送", source)
        self.assertIn("名称/备注", source)
        self.assertIn("轮间隔", source)
        self.assertNotIn('勾选"循环发送"', source)
        self.assertNotIn("右键点击", source)


if __name__ == "__main__":
    unittest.main()
