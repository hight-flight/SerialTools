"""
自动应答功能

功能特点：
- 当接收到特定数据时，自动发送预设的响应内容
- 支持HEX匹配、文本包含、文本完全匹配三种模式
- 支持规则管理（添加、编辑、删除、排序）
- 规则日志记录所有触发事件
- 全局开关和响应延迟控制
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QPushButton, QLineEdit, QTextEdit,
                             QMessageBox, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView, QGroupBox,
                             QFrame, QCheckBox, QSpinBox)
from PyQt5.QtCore import Qt, QTimer, QMutexLocker
from PyQt5.QtGui import QFont, QTextCursor

from theme import apply_dialog_theme, DataReceiver, unescape_text


class AutoReplyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self._rules = []
        self._editing_index = None  # None=新增模式, int=编辑第N条规则
        self._data_buffer = b''
        self._data_receiver = DataReceiver()
        self._data_receiver.data_received.connect(self._process_receive_data)
        self._init_ui()
        self.setAttribute(Qt.WA_DeleteOnClose)

    def _init_ui(self):
        self.setWindowTitle("自动应答配置")
        self.setMinimumSize(750, 550)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._build_global_options(layout)
        self._build_rules_table(layout)
        self._build_rule_editor(layout)
        self._build_log_area(layout)
        self._build_buttons_bar(layout)

    def _build_global_options(self, parent_layout):
        global_group = QGroupBox("全局设置")
        global_layout = QHBoxLayout(global_group)
        global_layout.setSpacing(16)

        self.check_enable = QCheckBox("启用自动应答")
        global_layout.addWidget(self.check_enable)

        global_layout.addWidget(QLabel("响应延迟:"))
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 5000)
        self.spin_delay.setValue(100)
        self.spin_delay.setSuffix(" ms")
        self.spin_delay.setMaximumWidth(120)
        global_layout.addWidget(self.spin_delay)

        self.check_case_ignore = QCheckBox("忽略大小写")
        global_layout.addWidget(self.check_case_ignore)

        self.check_stop_after_match = QCheckBox("匹配后停止")
        global_layout.addWidget(self.check_stop_after_match)

        self.check_log_to_receive = QCheckBox("发送后记录到接收区")
        global_layout.addWidget(self.check_log_to_receive)

        self.check_newline = QCheckBox("回车换行")
        global_layout.addWidget(self.check_newline)

        global_layout.addStretch()
        parent_layout.addWidget(global_group)

    def _build_rules_table(self, parent_layout):
        table_group = QGroupBox("应答规则列表")
        table_layout = QVBoxLayout(table_group)
        table_layout.setSpacing(6)

        self.table_rules = QTableWidget()
        self.table_rules.setColumnCount(4)
        self.table_rules.setHorizontalHeaderLabels(["序号", "触发条件", "匹配模式", "响应内容"])
        self.table_rules.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_rules.setMinimumHeight(150)
        self.table_rules.itemSelectionChanged.connect(self._on_rule_selected)
        table_layout.addWidget(self.table_rules)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        btn_add = QPushButton("添加规则")
        btn_add.setMinimumWidth(80)
        btn_add.clicked.connect(self._add_rule)
        btn_layout.addWidget(btn_add)

        btn_edit = QPushButton("编辑规则")
        btn_edit.setMinimumWidth(80)
        btn_edit.clicked.connect(self._edit_rule)
        btn_layout.addWidget(btn_edit)

        btn_delete = QPushButton("删除规则")
        btn_delete.setMinimumWidth(80)
        btn_delete.clicked.connect(self._delete_rule)
        btn_layout.addWidget(btn_delete)

        btn_up = QPushButton("上移")
        btn_up.setMinimumWidth(60)
        btn_up.clicked.connect(self._move_rule_up)
        btn_layout.addWidget(btn_up)

        btn_down = QPushButton("下移")
        btn_down.setMinimumWidth(60)
        btn_down.clicked.connect(self._move_rule_down)
        btn_layout.addWidget(btn_down)

        btn_layout.addStretch()
        table_layout.addLayout(btn_layout)

        parent_layout.addWidget(table_group)

    def _build_rule_editor(self, parent_layout):
        editor_group = QGroupBox("规则编辑")
        editor_layout = QVBoxLayout(editor_group)
        editor_layout.setSpacing(6)

        # 模式指示器 + 操作按钮
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(8)
        self.lbl_edit_mode = QLabel("")
        self.lbl_edit_mode.setStyleSheet("color: #528BFF; font-weight: bold;")
        mode_layout.addWidget(self.lbl_edit_mode)
        mode_layout.addStretch()
        self.btn_save_edit = QPushButton("保存修改")
        self.btn_save_edit.setMinimumWidth(80)
        self.btn_save_edit.clicked.connect(self._save_edit)
        self.btn_save_edit.setVisible(False)
        mode_layout.addWidget(self.btn_save_edit)
        self.btn_cancel_edit = QPushButton("取消编辑")
        self.btn_cancel_edit.setMinimumWidth(80)
        self.btn_cancel_edit.clicked.connect(self._cancel_edit)
        self.btn_cancel_edit.setVisible(False)
        mode_layout.addWidget(self.btn_cancel_edit)
        editor_layout.addLayout(mode_layout)

        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(8)

        row1_layout.addWidget(QLabel("触发条件:"))
        self.edit_trigger = QLineEdit()
        self.edit_trigger.setFont(QFont("Consolas", 9))
        self.edit_trigger.setPlaceholderText("输入触发条件")
        row1_layout.addWidget(self.edit_trigger)

        row1_layout.addWidget(QLabel("匹配模式:"))
        self.combo_match_mode = QComboBox()
        self.combo_match_mode.addItems(["文本包含", "文本完全", "HEX匹配"])
        self.combo_match_mode.currentTextChanged.connect(self._on_match_mode_changed)
        row1_layout.addWidget(self.combo_match_mode)

        editor_layout.addLayout(row1_layout)

        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(8)

        row2_layout.addWidget(QLabel("响应内容:"))
        self.edit_response = QLineEdit()
        self.edit_response.setFont(QFont("Consolas", 9))
        self.edit_response.setPlaceholderText("输入响应内容")
        row2_layout.addWidget(self.edit_response)

        row2_layout.addWidget(QLabel("响应格式:"))
        self.combo_response_format = QComboBox()
        self.combo_response_format.addItems(["文本", "HEX"])
        row2_layout.addWidget(self.combo_response_format)

        editor_layout.addLayout(row2_layout)

        row3_layout = QHBoxLayout()
        row3_layout.setSpacing(8)

        self.check_enabled = QCheckBox("启用此规则")
        self.check_enabled.setChecked(True)
        row3_layout.addWidget(self.check_enabled)

        row3_layout.addWidget(QLabel("最大响应次数:"))
        self.spin_max_count = QSpinBox()
        self.spin_max_count.setRange(0, 9999)
        self.spin_max_count.setValue(0)
        self.spin_max_count.setSuffix(" (0=不限)")
        self.spin_max_count.setMaximumWidth(150)
        row3_layout.addWidget(self.spin_max_count)

        row3_layout.addStretch()
        editor_layout.addLayout(row3_layout)

        parent_layout.addWidget(editor_group)

    def _build_log_area(self, parent_layout):
        log_group = QGroupBox("规则日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setSpacing(6)

        self.text_log = QTextEdit()
        self.text_log.setFont(QFont("Consolas", 9))
        self.text_log.setReadOnly(True)
        self.text_log.setMinimumHeight(100)
        log_layout.addWidget(self.text_log)

        log_btn_layout = QHBoxLayout()
        self.check_pause_scroll = QCheckBox("暂停自动滚动")
        log_btn_layout.addWidget(self.check_pause_scroll)
        log_btn_layout.addStretch()

        btn_clear_log = QPushButton("清空日志")
        btn_clear_log.clicked.connect(self._clear_log)
        log_btn_layout.addWidget(btn_clear_log)

        log_layout.addLayout(log_btn_layout)
        parent_layout.addWidget(log_group)

    def _build_buttons_bar(self, parent_layout):
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(16)

        btn_layout.addStretch()

        btn_save = QPushButton("保存规则")
        btn_save.setMinimumWidth(80)
        btn_save.clicked.connect(self._save_rules)
        btn_layout.addWidget(btn_save)

        btn_load = QPushButton("加载规则")
        btn_load.setMinimumWidth(80)
        btn_load.clicked.connect(self._load_rules)
        btn_layout.addWidget(btn_load)

        btn_close = QPushButton("关闭")
        btn_close.setMinimumWidth(80)
        btn_close.clicked.connect(self.close)
        btn_layout.addWidget(btn_close)

        parent_layout.addLayout(btn_layout)

    def _add_rule(self):
        """添加新规则（或编辑模式下保存修改）。"""
        trigger = self.edit_trigger.text().strip()
        response = self.edit_response.text().strip()

        if not trigger:
            QMessageBox.warning(self, "输入错误", "请输入触发条件")
            return
        if not response:
            QMessageBox.warning(self, "输入错误", "请输入响应内容")
            return

        rule = {
            'trigger': trigger,
            'match_mode': self.combo_match_mode.currentText(),
            'response': response,
            'response_format': self.combo_response_format.currentText(),
            'enabled': self.check_enabled.isChecked(),
            'max_count': self.spin_max_count.value(),
            'count': 0
        }

        if self._editing_index is not None:
            # 编辑模式：更新已有规则
            old_count = self._rules[self._editing_index]['count']
            rule['count'] = old_count
            self._rules[self._editing_index] = rule
            self._append_log(f"规则修改: {trigger}")
        else:
            # 新增模式
            self._rules.append(rule)
            self._append_log(f"规则添加: {trigger}")

        self._refresh_rules_table()
        self._clear_editor()

    def _save_edit(self):
        """编辑模式下点击「保存修改」按钮。"""
        self._add_rule()

    def _cancel_edit(self):
        """退出编辑模式，清空编辑器。"""
        self._clear_editor()

    def _enter_edit_mode(self, row):
        """进入编辑模式，填充指定规则到编辑器。"""
        if row < 0 or row >= len(self._rules):
            return
        self._editing_index = row
        rule = self._rules[row]
        self.edit_trigger.setText(rule['trigger'])
        self.edit_response.setText(rule['response'])
        self.combo_match_mode.setCurrentText(rule['match_mode'])
        self.combo_response_format.setCurrentText(rule['response_format'])
        self.check_enabled.setChecked(rule['enabled'])
        self.spin_max_count.setValue(rule['max_count'])
        self._update_edit_mode_ui()

    def _update_edit_mode_ui(self):
        """根据编辑状态切换编辑器 UI。"""
        if self._editing_index is not None:
            self.lbl_edit_mode.setText(f"● 编辑模式 — 正在修改规则 #{self._editing_index + 1}")
            self.btn_save_edit.setVisible(True)
            self.btn_cancel_edit.setVisible(True)
        else:
            self.lbl_edit_mode.setText("")
            self.btn_save_edit.setVisible(False)
            self.btn_cancel_edit.setVisible(False)

    def _edit_rule(self):
        """响应「编辑规则」按钮：进入编辑模式。"""
        row = self.table_rules.currentRow()
        if row < 0:
            QMessageBox.warning(self, "操作错误", "请先选中要编辑的规则")
            return
        self._enter_edit_mode(row)

    def _delete_rule(self):
        row = self.table_rules.currentRow()
        if row < 0:
            QMessageBox.warning(self, "操作错误", "请先选中要删除的规则")
            return

        rule = self._rules[row]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除规则「{rule['trigger']}」吗？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        del self._rules[row]
        self._refresh_rules_table()
        self._clear_editor()
        self._append_log(f"规则删除: {rule['trigger']}")

    def _move_rule_up(self):
        row = self.table_rules.currentRow()
        if row <= 0:
            return

        self._rules[row], self._rules[row-1] = self._rules[row-1], self._rules[row]
        self._refresh_rules_table()
        self.table_rules.selectRow(row-1)

    def _move_rule_down(self):
        row = self.table_rules.currentRow()
        if row < 0 or row >= len(self._rules) - 1:
            return

        self._rules[row], self._rules[row+1] = self._rules[row+1], self._rules[row]
        self._refresh_rules_table()
        self.table_rules.selectRow(row+1)

    def _on_rule_selected(self):
        row = self.table_rules.currentRow()
        if row >= 0 and row < len(self._rules):
            self._enter_edit_mode(row)

    def _on_match_mode_changed(self, mode):
        if mode == "HEX匹配":
            self.edit_trigger.setPlaceholderText("输入HEX触发条件，如: 01 03")
        else:
            self.edit_trigger.setPlaceholderText("输入触发条件")

    def _refresh_rules_table(self):
        self.table_rules.setRowCount(0)

        # 空状态提示
        if not self._rules:
            self.table_rules.setRowCount(1)
            empty_item = QTableWidgetItem("暂无规则，请在下方编辑器中填写条件后点击「添加规则」")
            empty_item.setForeground(Qt.gray)
            empty_item.setTextAlignment(Qt.AlignCenter)
            self.table_rules.setItem(0, 0, empty_item)
            # 合并单元格显示提示
            self.table_rules.setSpan(0, 0, 1, 4)
            return

        for i, rule in enumerate(self._rules):
            row = self.table_rules.rowCount()
            self.table_rules.insertRow(row)
            self.table_rules.setItem(row, 0, QTableWidgetItem(str(i+1)))
            trigger_display = rule['trigger']
            if not rule['enabled']:
                trigger_display += " (已禁用)"
            self.table_rules.setItem(row, 1, QTableWidgetItem(trigger_display))
            self.table_rules.setItem(row, 2, QTableWidgetItem(rule['match_mode']))

            response_display = rule['response']
            if len(response_display) > 30:
                response_display = response_display[:30] + "..."
            self.table_rules.setItem(row, 3, QTableWidgetItem(response_display))

            if not rule['enabled']:
                for col in range(4):
                    item = self.table_rules.item(row, col)
                    if item:
                        item.setForeground(Qt.gray)

    def _clear_editor(self):
        self._editing_index = None
        self._update_edit_mode_ui()
        self.edit_trigger.clear()
        self.edit_response.clear()
        self.combo_match_mode.setCurrentIndex(0)
        self.combo_response_format.setCurrentIndex(0)
        self.check_enabled.setChecked(True)
        self.spin_max_count.setValue(0)
        # 清除表格选中状态
        self.table_rules.clearSelection()

    def _append_log(self, text):
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.text_log.append(f"[{timestamp}] {text}")
        if not self.check_pause_scroll.isChecked():
            cursor = self.text_log.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.text_log.setTextCursor(cursor)

    def _clear_log(self):
        self.text_log.clear()

    def _save_rules(self):
        import json
        import os

        rules_data = []
        for rule in self._rules:
            rules_data.append({
                'trigger': rule['trigger'],
                'match_mode': rule['match_mode'],
                'response': rule['response'],
                'response_format': rule['response_format'],
                'enabled': rule['enabled'],
                'max_count': rule['max_count']
            })

        config = {
            'rules': rules_data,
            'global': {
                'enabled': self.check_enable.isChecked(),
                'delay': self.spin_delay.value(),
                'case_ignore': self.check_case_ignore.isChecked(),
                'stop_after_match': self.check_stop_after_match.isChecked(),
                'log_to_receive': self.check_log_to_receive.isChecked(),
                'newline': self.check_newline.isChecked(),
            }
        }

        config_dir = os.path.join(os.path.expanduser('~'), '.serial_GUI')
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, 'auto_reply_rules.json')

        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self._append_log("规则已保存")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"保存规则失败: {e}")

    def _load_rules(self):
        import json
        import os

        config_dir = os.path.join(os.path.expanduser('~'), '.serial_GUI')
        config_path = os.path.join(config_dir, 'auto_reply_rules.json')

        if not os.path.exists(config_path):
            QMessageBox.warning(self, "加载失败", "未找到规则配置文件")
            return

        # 如果当前有未保存的规则，提示确认
        if self._rules:
            reply = QMessageBox.question(
                self, "确认加载",
                "加载规则将覆盖当前规则列表，是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 兼容旧格式（纯规则列表）和新格式（含 global 配置）
            if isinstance(data, list):
                rules_data = data
                global_settings = {}
            else:
                rules_data = data.get('rules', [])
                global_settings = data.get('global', {})

            # 先构建临时列表，成功后再替换，避免加载失败清空现有规则
            new_rules = []
            for r in rules_data:
                new_rules.append({
                    'trigger': r.get('trigger', ''),
                    'match_mode': r.get('match_mode', '文本包含'),
                    'response': r.get('response', ''),
                    'response_format': r.get('response_format', '文本'),
                    'enabled': r.get('enabled', True),
                    'max_count': r.get('max_count', 0),
                    'count': 0
                })

            self._rules = new_rules

            # 恢复全局设置
            if global_settings:
                self.check_enable.setChecked(global_settings.get('enabled', False))
                self.spin_delay.setValue(global_settings.get('delay', 100))
                self.check_case_ignore.setChecked(global_settings.get('case_ignore', False))
                self.check_stop_after_match.setChecked(global_settings.get('stop_after_match', False))
                self.check_log_to_receive.setChecked(global_settings.get('log_to_receive', False))
                self.check_newline.setChecked(global_settings.get('newline', False))

            self._refresh_rules_table()
            self._clear_editor()
            self._append_log(f"规则已加载（{len(new_rules)} 条）")
        except Exception as e:
            QMessageBox.warning(self, "加载失败", f"加载规则失败: {e}")

    def handle_receive_data(self, data):
        if not self.check_enable.isChecked():
            return
        self._data_receiver.data_received.emit(data)

    def _process_receive_data(self, data):
        self._data_buffer += data

        try:
            encoding = getattr(self.parent_window, 'combo_encoding', None)
            if encoding:
                encoding = encoding.currentText()
            else:
                encoding = 'UTF-8'

            try:
                text = self._data_buffer.decode(encoding, errors='replace')
            except LookupError:
                text = self._data_buffer.decode('utf-8', errors='replace')

            hex_str = ' '.join([f'{byte:02X}' for byte in self._data_buffer])

            matched_any = False
            for rule in self._rules:
                if not rule['enabled']:
                    continue

                if rule['max_count'] > 0 and rule['count'] >= rule['max_count']:
                    continue

                matched = False

                if rule['match_mode'] == 'HEX匹配':
                    trigger_hex = rule['trigger'].replace(' ', '')
                    data_hex = hex_str.replace(' ', '')
                    if trigger_hex in data_hex:
                        matched = True
                elif rule['match_mode'] == '文本包含':
                    trigger = unescape_text(rule['trigger'])
                    if self.check_case_ignore.isChecked():
                        if trigger.lower() in text.lower():
                            matched = True
                    else:
                        if trigger in text:
                            matched = True
                elif rule['match_mode'] == '文本完全':
                    trigger = unescape_text(rule['trigger'])
                    stripped_text = text.strip()
                    if self.check_case_ignore.isChecked():
                        if trigger.lower() == stripped_text.lower():
                            matched = True
                    else:
                        if trigger == stripped_text:
                            matched = True

                if matched:
                    matched_any = True
                    rule['count'] += 1
                    self._append_log(f"规则{self._rules.index(rule)+1}触发: 收到 \"{text.strip()[:30]}...\"")
                    self._send_response(rule)

                    if self.check_stop_after_match.isChecked():
                        break

            # 匹配成功后清空缓冲区，避免已匹配数据被重复扫描
            if matched_any:
                self._data_buffer = b''
            elif len(self._data_buffer) > 4096:
                self._data_buffer = b''

        except Exception:
            pass

    def _send_response(self, rule):
        try:
            if rule['response_format'] == 'HEX':
                hex_str = rule['response'].replace(' ', '')
                response_data = bytes.fromhex(hex_str)
            else:
                # 处理转义序列（\r \n \t \\ → 实际控制字符）
                response_text = unescape_text(rule['response'])
                # 处理回车换行
                if self.check_newline.isChecked():
                    response_text = response_text.rstrip('\r\n') + '\r\n'
                response_data = response_text.encode('utf-8')

            delay = self.spin_delay.value()
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda rd=response_data: self._do_send(rd))
            timer.start(delay)

            self._append_log(f"发送响应: {rule['response'][:50]}...")

        except ValueError:
            self._append_log(f"响应格式错误: {rule['response']}")

    def _do_send(self, data):
        # 对话框已关闭或正在关闭时不再发送，避免访问已销毁的 C++ 对象
        if getattr(self, '_closing', False):
            return
        try:
            with QMutexLocker(self.parent_window.serial_mutex):
                self.parent_window.transport.write(data)

            if self.check_log_to_receive.isChecked():
                try:
                    text = data.decode('utf-8', errors='replace')
                    self.parent_window.append_text(text)
                except:
                    hex_str = ' '.join([f'{byte:02X}' for byte in data])
                    self.parent_window.append_text(hex_str)
        except Exception as e:
            self._append_log(f"发送失败: {e}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._closing = True
        if hasattr(self.parent_window, '_auto_reply_dialog'):
            self.parent_window._auto_reply_dialog = None
        event.accept()