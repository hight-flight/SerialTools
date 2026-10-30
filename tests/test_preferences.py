import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QTextEdit

from tests import test_window_placement
from serial_GUI import SerialTool
from preferences import DEFAULT_PREFERENCES, Preferences, PreferencesDialog, format_timestamp


ORIGINAL_LOAD_CONFIG = SerialTool.load_config


class PreferenceValueTests(unittest.TestCase):
    def test默认值保持现有版本行为(self):
        self.assertEqual(DEFAULT_PREFERENCES.display_timestamp, 'date_us')
        self.assertEqual(DEFAULT_PREFERENCES.log_timestamp, 'date_us')
        self.assertEqual(DEFAULT_PREFERENCES.receive_font, 'Consolas')
        self.assertEqual(DEFAULT_PREFERENCES.receive_font_size, 11)
        self.assertEqual(DEFAULT_PREFERENCES.max_display_lines, 5000)
        self.assertEqual(DEFAULT_PREFERENCES.resume_delay_seconds, 10)
        self.assertTrue(DEFAULT_PREFERENCES.word_wrap)
        self.assertTrue(DEFAULT_PREFERENCES.auto_scroll)
        self.assertTrue(DEFAULT_PREFERENCES.auto_resume)

    def test时间格式保持原始采集时间并正确截取毫秒(self):
        stamp = '[2026-10-30 14:25:36.123456] '
        for kind, expected in (
            ('time', '[14:25:36] '),
            ('time_ms', '[14:25:36.123] '),
            ('date_ms', '[2026-10-30 14:25:36.123] '),
            ('date_us', stamp),
        ):
            self.assertEqual(format_timestamp(stamp, kind), expected)
        self.assertEqual(format_timestamp('[测试时间] ', 'time'), '[测试时间] ')

    def test损坏与缺失配置回退默认值(self):
        for data in (None, [], '', {'receive_font_size': '大', 'max_display_lines': -1,
                                   'auto_scroll': 'false', 'display_timestamp': 'bad'}):
            self.assertEqual(Preferences.from_dict(data), DEFAULT_PREFERENCES)
        value = Preferences.from_dict({'word_wrap': False, 'unknown': 123})
        self.assertFalse(value.word_wrap)
        self.assertEqual(value.max_display_lines, 5000)


class PreferencesIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_window_placement.WindowPlacementTests.setUpClass()
        cls.app = test_window_placement.WindowPlacementTests.app

    def setUp(self):
        fixture = test_window_placement.WindowPlacementTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.tool = fixture.tool

    def dialog(self):
        dialog = PreferencesDialog(self.tool)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test设置菜单位于帮助之前(self):
        labels = [a.text().split('(')[0] for a in self.tool.menuBar().actions()]
        self.assertEqual(labels, ['文件', '视图', '工具', '设置', '帮助'])

    def test界面与日志时间戳独立且旧文本不改写(self):
        self.tool.text_recv.setPlainText('已有内容\n')
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, display_timestamp='time_ms'), persist=False)
        stamp = '[2026-10-30 14:25:36.123456] '
        _, entry = self.tool._build_log_entry(stamp, '数据')
        self.assertEqual(entry, stamp + '数据')
        self.tool._update_receive_display('数据', stamp)
        self.tool._flush_pending_display()
        self.assertIn('[14:25:36.123] 数据', self.tool.text_recv.toPlainText())
        self.assertIn('已有内容', self.tool.text_recv.toPlainText())
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, log_timestamp='date_ms'), persist=False)
        self.assertEqual(self.tool._build_log_entry(stamp, '数据')[1], '[2026-10-30 14:25:36.123] 数据')

    def test保存并重新加载偏好及旧配置兼容(self):
        prefs = replace(DEFAULT_PREFERENCES, receive_font_size=14, word_wrap=False,
                        auto_scroll=False, show_timestamp=False, max_display_lines=1000)
        self.assertTrue(self.tool.apply_preferences(prefs))
        saved = json.loads(Path(self.tool.config_file).read_text(encoding='utf-8'))
        self.assertEqual(saved['preferences'], prefs.to_dict())
        self.tool.apply_preferences(DEFAULT_PREFERENCES, persist=False)
        ORIGINAL_LOAD_CONFIG(self.tool)
        self.assertEqual(self.tool.current_preferences(), prefs)
        self.assertEqual(self.tool.text_recv.lineWrapMode(), QTextEdit.NoWrap)
        saved.pop('preferences')
        Path(self.tool.config_file).write_text(json.dumps(saved), encoding='utf-8')
        ORIGINAL_LOAD_CONFIG(self.tool)
        self.assertEqual(self.tool.current_preferences(), replace(DEFAULT_PREFERENCES, show_timestamp=False))

    def test启动加载自动日志时不会覆盖已保存偏好(self):
        prefs = replace(DEFAULT_PREFERENCES, receive_font_size=16, display_timestamp='time_ms')
        self.tool.apply_preferences(prefs)
        path = Path(self.tool.config_file)
        saved = json.loads(path.read_text(encoding='utf-8'))
        saved['auto_save'] = True
        path.write_text(json.dumps(saved), encoding='utf-8')
        self.tool.apply_preferences(DEFAULT_PREFERENCES, persist=False)
        ORIGINAL_LOAD_CONFIG(self.tool)
        self.assertEqual(self.tool.current_preferences(), prefs)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['preferences'], prefs.to_dict())

    def test取消不应用而恢复默认立即保存且保留通信配置(self):
        prefs = replace(DEFAULT_PREFERENCES, receive_font_size=16, auto_resume=False)
        self.tool.apply_preferences(prefs)
        self.tool.combo_baud.setCurrentText('9600')
        dialog = self.dialog()
        dialog.font_size.setValue(20)
        dialog.reject()
        self.assertEqual(self.tool.current_preferences(), prefs)
        dialog = self.dialog()
        dialog.restore_defaults()
        self.assertEqual(self.tool.current_preferences(), DEFAULT_PREFERENCES)
        saved = json.loads(Path(self.tool.config_file).read_text(encoding='utf-8'))
        base = json.loads(Path(self.tool._base_config_file).read_text(encoding='utf-8'))
        self.assertEqual(saved['preferences'], DEFAULT_PREFERENCES.to_dict())
        self.assertEqual(base['preferences'], DEFAULT_PREFERENCES.to_dict())
        self.assertEqual(str(saved['baudrate']), '9600')

    def test关闭跟随时新内容不会把阅读位置拉到底部(self):
        self.tool.show()
        self.tool.text_recv.setPlainText('\n'.join(str(i) for i in range(200)))
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, auto_scroll=False), persist=False)
        bar = self.tool.text_recv.verticalScrollBar()
        bar.setValue(0)
        self.tool.append_text('新消息')
        self.tool._update_receive_display('接收内容', '[2026-10-30 14:25:36.123456] ')
        self.tool._flush_pending_display()
        self.assertEqual(bar.value(), 0)

    def test手动查阅可关闭自动恢复或设置等待时间(self):
        self.tool.show()
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, auto_resume=False), persist=False)
        event = mock.Mock()
        event.angleDelta.return_value.y.return_value = 120
        self.tool._on_recv_wheel(event)
        self.assertTrue(self.tool._recv_user_reading)
        self.assertFalse(self.tool._recv_follow_timer.isActive())
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, resume_delay_seconds=3), persist=False)
        self.assertEqual(self.tool._recv_follow_timer.interval(), 3000)
        self.tool._on_recv_wheel(event)
        self.assertTrue(self.tool._recv_follow_timer.isActive())

    def test显示容量不清除日志且字体在换主题后保持(self):
        self.tool.log_buffer.append('保留日志')
        self.tool.text_recv.setPlainText('\n'.join(str(i) for i in range(1500)))
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, max_display_lines=500,
                                            receive_font_size=14), persist=False)
        self.assertLessEqual(self.tool.text_recv.document().blockCount(), 500)
        self.assertIn('1499', self.tool.text_recv.toPlainText())
        self.assertEqual(list(self.tool.log_buffer), ['保留日志'])
        self.tool.apply_theme('dark')
        self.app.processEvents()
        self.assertEqual(self.tool.text_recv.font().pointSize(), 14)

    def test一次收到大量内容也遵守显示上限(self):
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, max_display_lines=500), persist=False)
        self.tool.text_recv.setPlainText('\n'.join(str(i) for i in range(1800)))
        self.tool._trim_recv_buffer()
        self.assertLessEqual(self.tool.text_recv.document().blockCount(), 500)
        self.assertIn('1799', self.tool.text_recv.toPlainText())

    def test关闭跟随时清理旧行不强制跳到底部(self):
        self.tool.show()
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, auto_scroll=False, max_display_lines=500), persist=False)
        self.tool.text_recv.setPlainText('\n'.join(str(i) for i in range(1500)))
        bar = self.tool.text_recv.verticalScrollBar()
        bar.setValue(0)
        self.tool._trim_recv_buffer(force=True)
        self.assertEqual(bar.value(), 0)

    def test清理旧行后仍显示原来正在阅读的内容(self):
        self.tool.show()
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, auto_scroll=False,
                                            max_display_lines=500), persist=False)
        edit = self.tool.text_recv
        edit.setPlainText('\n'.join(f'第{i}行' for i in range(600)))
        cursor = edit.textCursor()
        cursor.setPosition(edit.document().findBlockByNumber(300).position())
        edit.setTextCursor(cursor)
        edit.ensureCursorVisible()
        self.app.processEvents()
        before = edit.cursorForPosition(QPoint(1, 1)).block().text()
        self.tool._trim_recv_buffer(force=True)
        self.assertEqual(edit.cursorForPosition(QPoint(1, 1)).block().text(), before)

    def test恰好达到上限时不提前删除内容(self):
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, max_display_lines=500), persist=False)
        self.tool.text_recv.setPlainText('\n'.join(str(i) for i in range(500)))
        self.tool._trim_recv_buffer()
        self.assertEqual(self.tool.text_recv.document().blockCount(), 500)

    def test系统和格式化消息追加后也遵守行数上限(self):
        from PyQt5.QtGui import QTextCharFormat
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, max_display_lines=500), persist=False)
        payload = '\n'.join(str(i) for i in range(700))
        self.tool.append_text('[系统]:' + payload)
        self.assertLessEqual(self.tool.text_recv.document().blockCount(), 500)
        self.tool.text_recv.clear()
        self.tool.append_formatted_text([(payload, QTextCharFormat())])
        self.assertLessEqual(self.tool.text_recv.document().blockCount(), 500)

    def test恢复布局保存且保留数据与连接模式(self):
        self.tool.show()
        self.tool.combo_mode.setCurrentIndex(1)
        self.tool.text_send.setPlainText('待发送内容')
        self.tool.toggle_multi_send()
        self.tool.io_splitter.setSizes([200, 500])
        self.tool.restore_default_layout()
        QTest.qWait(80)
        self.assertTrue(self.tool.main_splitter.widget(1).isHidden())
        self.assertEqual(self.tool.text_send.height(), 64)
        self.assertEqual(self.tool.text_send.toPlainText(), '待发送内容')
        self.assertEqual(self.tool.connection_mode, 'udp')
        self.assertTrue(Path(self.tool.config_file).exists())

    def test保存失败时设置窗口不关闭且允许重试(self):
        dialog = self.dialog()
        dialog.show()
        dialog.font_size.setValue(15)
        with mock.patch('shutil.copy2', side_effect=OSError('模拟配置同步失败')), \
                mock.patch('preferences.QMessageBox.warning') as warning:
            dialog._accept_changes()
            self.assertTrue(dialog.isVisible())
            warning.assert_called_once()
            self.assertEqual(self.tool.current_preferences().receive_font_size, 15)
        dialog._accept_changes()
        self.assertFalse(dialog.isVisible())
        saved = json.loads(Path(self.tool._base_config_file).read_text(encoding='utf-8'))
        self.assertEqual(saved['preferences']['receive_font_size'], 15)

    def test修改时间格式前待显示的数据仍使用旧格式(self):
        stamp = '[2026-10-30 14:25:36.123456] '
        self.tool._pending_display_data = [('修改前', stamp)]
        self.tool.apply_preferences(replace(DEFAULT_PREFERENCES, display_timestamp='time'), persist=False)
        self.tool._update_receive_display('修改后', stamp)
        self.tool._flush_pending_display()
        content = self.tool.text_recv.toPlainText()
        self.assertIn(stamp + '修改前', content)
        self.assertIn('[14:25:36] 修改后', content)

    def test设置窗口确定与应用可操作(self):
        dialog = self.dialog()
        dialog.font_size.setValue(13)
        self.assertTrue(dialog.apply_changes())
        self.assertEqual(self.tool.current_preferences().receive_font_size, 13)
        dialog.reject()
        self.assertEqual(self.tool.current_preferences().receive_font_size, 13)


if __name__ == '__main__':
    unittest.main()
