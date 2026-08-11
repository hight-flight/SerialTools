"""
自动应答功能

功能特点：
- 当接收到特定数据时，自动发送预设的响应内容
- 支持HEX匹配、文本包含、文本完全匹配三种模式
- 支持规则管理（添加、编辑、删除、排序）
- 规则日志记录所有触发事件
- 全局开关和响应延迟控制
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                             QComboBox, QPushButton, QLineEdit, QTextEdit,
                             QMessageBox, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView, QGroupBox,
                             QFrame, QCheckBox, QSpinBox, QFileDialog,
                             QScrollArea, QSplitter, QWidget)
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
        self._response_timers = set()
        self._closing = False
        import os
        self._last_rules_dir = os.path.join(os.path.expanduser('~'), '.serial_GUI')
        self._init_ui()
        self._clean_state = self._serialize_persistent_state()
        self.setAttribute(Qt.WA_DeleteOnClose)

    def _init_ui(self):
        self.setWindowTitle("自动应答配置")
        self.resize(780, 760)
        self.setMinimumSize(560, 420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(4, 4, 4, 4)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setAccessibleName("自动应答配置内容")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)

        self._build_global_options(layout)

        self.reply_sections_splitter = QSplitter(Qt.Vertical)
        self.reply_sections_splitter.setObjectName("replySectionsSplitter")
        self.reply_sections_splitter.setChildrenCollapsible(False)
        for builder in (
                self._build_rules_table,
                self._build_rule_editor,
                self._build_log_area):
            section = QWidget()
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            builder(section_layout)
            self.reply_sections_splitter.addWidget(section)
        self.reply_sections_splitter.setSizes([220, 135, 205])
        layout.addWidget(self.reply_sections_splitter, 1)

        self._build_buttons_bar(layout)
        self.scroll_area.setWidget(content)
        outer_layout.addWidget(self.scroll_area)
        self._refresh_rules_table()

    def _build_global_options(self, parent_layout):
        global_group = QGroupBox("全局设置")
        global_layout = QGridLayout(global_group)
        global_layout.setHorizontalSpacing(16)
        global_layout.setVerticalSpacing(8)

        self.check_enable = QCheckBox("启用自动应答")
        global_layout.addWidget(self.check_enable, 0, 0)

        global_layout.addWidget(QLabel("响应延迟："), 0, 1)
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 5000)
        self.spin_delay.setValue(100)
        self.spin_delay.setSuffix(" ms")
        self.spin_delay.setMaximumWidth(120)
        self.spin_delay.setAccessibleName("响应延迟")
        global_layout.addWidget(self.spin_delay, 0, 2)

        self.lbl_connection_status = QLabel()
        self.lbl_connection_status.setAccessibleName("自动应答连接状态")
        global_layout.addWidget(self.lbl_connection_status, 0, 3)
        self._connection_timer = QTimer(self)
        self._connection_timer.timeout.connect(self._refresh_connection_status)
        self._connection_timer.start(500)
        self._refresh_connection_status()

        self.check_case_ignore = QCheckBox("忽略大小写")
        global_layout.addWidget(self.check_case_ignore, 1, 0)

        self.check_stop_after_match = QCheckBox("匹配后停止")
        global_layout.addWidget(self.check_stop_after_match, 1, 1)

        self.check_log_to_receive = QCheckBox("发送后记录到接收区")
        global_layout.addWidget(self.check_log_to_receive, 1, 2)

        global_layout.setColumnStretch(3, 1)
        parent_layout.addWidget(global_group)

    def _build_rules_table(self, parent_layout):
        table_group = QGroupBox("应答规则列表")
        table_layout = QVBoxLayout(table_group)
        table_layout.setSpacing(6)

        self.table_rules = QTableWidget()
        self.table_rules.setAccessibleName("自动应答规则列表")
        self.table_rules.setColumnCount(4)
        self.table_rules.verticalHeader().setVisible(False)
        self.table_rules.setHorizontalHeaderLabels(["序号", "触发条件", "匹配模式", "响应内容"])
        self.table_rules.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_rules.setMinimumHeight(150)
        self.table_rules.itemSelectionChanged.connect(self._on_rule_selected)
        self.table_rules.itemDoubleClicked.connect(lambda _item: self._edit_rule())
        table_layout.addWidget(self.table_rules)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_edit_rule = QPushButton("编辑规则")
        self.btn_edit_rule.setMinimumWidth(80)
        self.btn_edit_rule.clicked.connect(self._edit_rule)
        btn_layout.addWidget(self.btn_edit_rule)

        self.btn_delete_rule = QPushButton("删除规则")
        self.btn_delete_rule.setMinimumWidth(80)
        self.btn_delete_rule.clicked.connect(self._delete_rule)
        btn_layout.addWidget(self.btn_delete_rule)

        self.btn_move_up = QPushButton("上移")
        self.btn_move_up.setMinimumWidth(60)
        self.btn_move_up.clicked.connect(self._move_rule_up)
        btn_layout.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("下移")
        self.btn_move_down.setMinimumWidth(60)
        self.btn_move_down.clicked.connect(self._move_rule_down)
        btn_layout.addWidget(self.btn_move_down)

        btn_layout.addStretch()
        table_layout.addLayout(btn_layout)

        parent_layout.addWidget(table_group)

    def _build_rule_editor(self, parent_layout):
        editor_group = QGroupBox("规则编辑")
        editor_layout = QVBoxLayout(editor_group)
        editor_layout.setContentsMargins(8, 6, 8, 6)
        editor_layout.setSpacing(4)

        # 模式指示器 + 操作按钮
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(8)
        self.lbl_edit_mode = QLabel("")
        self.lbl_edit_mode.setStyleSheet("color: #528BFF; font-weight: bold;")
        self.lbl_edit_mode.setVisible(False)
        mode_layout.addWidget(self.lbl_edit_mode)
        self.lbl_validation = QLabel("")
        self.lbl_validation.setAccessibleName("规则输入校验结果")
        self.lbl_validation.setVisible(False)
        mode_layout.addWidget(self.lbl_validation)
        mode_layout.addStretch()
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

        self.check_newline = QCheckBox("回车换行")
        row2_layout.addWidget(self.check_newline)

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
        self.spin_max_count.setAccessibleName("最大响应次数")
        row3_layout.addWidget(self.spin_max_count)

        row3_layout.addStretch()

        self.btn_save_edit = QPushButton("添加到列表")
        self.btn_save_edit.setMinimumWidth(80)
        self.btn_save_edit.clicked.connect(self._add_rule)
        row3_layout.addWidget(self.btn_save_edit)

        self.btn_cancel_edit = QPushButton("取消编辑")
        self.btn_cancel_edit.setMinimumWidth(80)
        self.btn_cancel_edit.clicked.connect(self._cancel_edit)
        self.btn_cancel_edit.setVisible(False)
        row3_layout.addWidget(self.btn_cancel_edit)
        editor_layout.addLayout(row3_layout)

        self.edit_trigger.textChanged.connect(self._validate_editor)
        self.edit_response.textChanged.connect(self._validate_editor)
        self.combo_match_mode.currentTextChanged.connect(self._validate_editor)
        self.combo_response_format.currentTextChanged.connect(self._validate_editor)
        self._validate_editor()

        parent_layout.addWidget(editor_group)

    def _build_log_area(self, parent_layout):
        log_group = QGroupBox("规则日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setSpacing(6)

        self.text_log = QTextEdit()
        self.text_log.setFont(QFont("Consolas", 9))
        self.text_log.setReadOnly(True)
        self.text_log.setMinimumHeight(160)
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

        btn_help = QPushButton("帮助")
        btn_help.setMinimumWidth(60)
        btn_help.clicked.connect(self._show_help)
        btn_layout.addWidget(btn_help)

        btn_layout.addStretch()

        btn_save = QPushButton("导出规则")
        btn_save.setMinimumWidth(80)
        btn_save.clicked.connect(self._save_rules)
        btn_layout.addWidget(btn_save)

        btn_load = QPushButton("导入规则")
        btn_load.setMinimumWidth(80)
        btn_load.clicked.connect(self._load_rules)
        btn_layout.addWidget(btn_load)

        btn_close = QPushButton("关闭")
        btn_close.setMinimumWidth(80)
        btn_close.clicked.connect(self.close)
        btn_layout.addWidget(btn_close)

        parent_layout.addLayout(btn_layout)

    def _refresh_connection_status(self):
        transport = getattr(self.parent_window, "transport", None)
        connected = bool(transport and getattr(transport, "is_open", False))
        self.lbl_connection_status.setText(
            "连接状态：已连接" if connected else "连接状态：未连接"
        )

    @staticmethod
    def _persistent_rule(rule):
        return {
            "trigger": rule.get("trigger", ""),
            "match_mode": rule.get("match_mode", "文本包含"),
            "response": rule.get("response", ""),
            "response_format": rule.get("response_format", "文本"),
            "enabled": rule.get("enabled", True),
            "max_count": rule.get("max_count", 0),
            "newline": rule.get("newline", False),
        }

    def _serialize_persistent_state(self):
        return {
            "rules": [self._persistent_rule(rule) for rule in self._rules],
            "global": {
                "delay": self.spin_delay.value(),
                "case_ignore": self.check_case_ignore.isChecked(),
                "stop_after_match": self.check_stop_after_match.isChecked(),
                "log_to_receive": self.check_log_to_receive.isChecked(),
            },
        }

    def _editor_rule(self):
        return {
            "trigger": self.edit_trigger.text().strip(),
            "match_mode": self.combo_match_mode.currentText(),
            "response": self.edit_response.text().strip(),
            "response_format": self.combo_response_format.currentText(),
            "enabled": self.check_enabled.isChecked(),
            "max_count": self.spin_max_count.value(),
            "newline": self.check_newline.isChecked(),
        }

    def _has_pending_editor(self):
        current = self._editor_rule()
        if self._editing_index is not None and 0 <= self._editing_index < len(self._rules):
            return current != self._persistent_rule(self._rules[self._editing_index])
        default = {
            "trigger": "", "match_mode": "文本包含", "response": "",
            "response_format": "文本", "enabled": True, "max_count": 0,
            "newline": False,
        }
        return current != default

    def _has_unsaved_changes(self):
        clean_state = getattr(self, "_clean_state", self._serialize_persistent_state())
        return self._serialize_persistent_state() != clean_state or self._has_pending_editor()

    @staticmethod
    def _is_valid_hex(value):
        try:
            compact = "".join(value.split())
            return bool(compact) and len(compact) % 2 == 0 and bool(bytes.fromhex(compact))
        except ValueError:
            return False

    def _validate_editor(self, *_args):
        trigger = self.edit_trigger.text().strip()
        response = self.edit_response.text().strip()
        error = ""
        if trigger and self.combo_match_mode.currentText() == "HEX匹配":
            if not self._is_valid_hex(trigger):
                error = "⚠ 触发条件不是有效的 HEX 数据"
        if not error and response and self.combo_response_format.currentText() == "HEX":
            if not self._is_valid_hex(response):
                error = "⚠ 响应内容不是有效的 HEX 数据"
        self.lbl_validation.setText(error)
        self.lbl_validation.setVisible(bool(error))
        valid = bool(trigger and response and not error)
        self.btn_save_edit.setEnabled(valid)
        return valid

    def _update_rule_action_states(self):
        row = self.table_rules.currentRow()
        has_rule = 0 <= row < len(self._rules)
        self.btn_edit_rule.setEnabled(has_rule)
        self.btn_delete_rule.setEnabled(has_rule)
        self.btn_move_up.setEnabled(has_rule and row > 0)
        self.btn_move_down.setEnabled(has_rule and row < len(self._rules) - 1)


    def _add_rule(self):
        """添加新规则（或编辑模式下保存修改）。"""
        if not self._validate_editor():
            return

        rule = self._editor_rule()
        rule["count"] = 0
        trigger = rule["trigger"]

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
        self.check_newline.setChecked(rule.get('newline', False))
        self._update_edit_mode_ui()

    def _update_edit_mode_ui(self):
        """根据编辑状态切换编辑器 UI。"""
        if self._editing_index is not None:
            self.lbl_edit_mode.setText(f"● 编辑模式 — 正在修改规则 #{self._editing_index + 1}")
            self.lbl_edit_mode.setVisible(True)
            self.btn_save_edit.setText("保存修改")
            self.btn_save_edit.setVisible(True)
            self.btn_cancel_edit.setVisible(True)
        else:
            self.lbl_edit_mode.setText("")
            self.lbl_edit_mode.setVisible(False)
            self.btn_save_edit.setText("添加到列表")
            self.btn_save_edit.setVisible(True)
            self.btn_cancel_edit.setVisible(False)

    def _edit_rule(self):
        """响应「编辑规则」按钮：进入编辑模式。"""
        row = self.table_rules.currentRow()
        if row < 0:
            QMessageBox.warning(self, "操作错误", "请先选中要编辑的规则")
            return
        if self._editing_index != row and self._has_pending_editor():
            reply = QMessageBox.question(
                self, "放弃当前编辑",
                "规则编辑区有尚未提交的内容，是否放弃并编辑所选规则？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
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

        editing_index = self._editing_index
        del self._rules[row]
        self._refresh_rules_table()
        if editing_index == row:
            self._clear_editor()
        elif editing_index is not None:
            if editing_index > row:
                self._editing_index -= 1
            self._update_edit_mode_ui()
        self._append_log(f"规则删除: {rule['trigger']}")

    def _move_rule_up(self):
        row = self.table_rules.currentRow()
        if row <= 0:
            return

        if self._editing_index == row:
            self._editing_index = row - 1
        elif self._editing_index == row - 1:
            self._editing_index = row
        self._rules[row], self._rules[row-1] = self._rules[row-1], self._rules[row]
        self._refresh_rules_table()
        self.table_rules.selectRow(row-1)
        self._update_edit_mode_ui()

    def _move_rule_down(self):
        row = self.table_rules.currentRow()
        if row < 0 or row >= len(self._rules) - 1:
            return

        if self._editing_index == row:
            self._editing_index = row + 1
        elif self._editing_index == row + 1:
            self._editing_index = row
        self._rules[row], self._rules[row+1] = self._rules[row+1], self._rules[row]
        self._refresh_rules_table()
        self.table_rules.selectRow(row+1)
        self._update_edit_mode_ui()

    def _on_rule_selected(self):
        self._update_rule_action_states()

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
            empty_item = QTableWidgetItem("暂无规则，请在下方编辑器中填写条件后点击「添加到列表」")
            empty_item.setForeground(Qt.gray)
            empty_item.setTextAlignment(Qt.AlignCenter)
            self.table_rules.setItem(0, 0, empty_item)
            # 合并单元格显示提示
            self.table_rules.setSpan(0, 0, 1, 4)
            self._update_rule_action_states()
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
            tags = []
            if rule.get('response_format') == 'HEX':
                tags.append("HEX")
            if rule.get('newline', False):
                tags.append("+\\r\\n")
            if tags:
                response_display += f" [{' '.join(tags)}]"
            self.table_rules.setItem(row, 3, QTableWidgetItem(response_display))

            if not rule['enabled']:
                for col in range(4):
                    item = self.table_rules.item(row, col)
                    if item:
                        item.setForeground(Qt.gray)

        self._update_rule_action_states()

    def _clear_editor(self):
        self._editing_index = None
        self._update_edit_mode_ui()
        self.edit_trigger.clear()
        self.edit_response.clear()
        self.combo_match_mode.setCurrentIndex(0)
        self.combo_response_format.setCurrentIndex(0)
        self.check_enabled.setChecked(True)
        self.spin_max_count.setValue(0)
        self.check_newline.setChecked(False)
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

    def _show_help(self):
        """显示自动应答功能帮助说明。"""
        help_text = (
            "自动应答功能使用说明\n"
            "═══════════════════════════════════════\n\n"
            "▎功能概述\n"
            "当串口接收到满足条件的特定数据时，自动发送预设的响应内容。\n\n"
            "▎全局设置\n"
            "• 启用自动应答：主开关，勾选后自动应答功能生效。\n"
            "  注：此状态为运行时状态，不随规则文件保存或恢复。\n"
            "• 响应延迟：收到触发数据后，延迟指定毫秒再发送响应。\n"
            "• 忽略大小写：文本匹配时忽略英文字母大小写（仅文本模式）。\n"
            "• 匹配后停止：命中一条规则后不再继续匹配后续规则。\n"
            "• 发送后记录到接收区：将自动发送的响应内容以[发送]标记显示在主窗口接收区。\n\n"
            "▎规则编辑\n"
            "• 触发条件：要匹配的数据内容。\n"
            "• 匹配模式：\n"
            "  - 文本包含：接收数据包含触发条件即匹配。\n"
            "  - 文本完全：接收数据与触发条件完全一致才匹配。\n"
            "  - HEX匹配：按十六进制数据匹配，如 01 03。\n"
            "• 响应内容：匹配成功后自动发送的数据。\n"
            "• 回车换行：自动在响应内容末尾添加 \\r\\n（逐条规则独立设置）。\n"
            "• 响应格式：文本（UTF-8编码）或 HEX（十六进制，如 01 02）。\n"
            "• 启用此规则：单条规则的开关。\n"
            "• 最大响应次数：达到次数后规则自动停用（0 表示不限）。\n\n"
            "▎规则列表标识\n"
            "响应内容列会显示以下标识：\n"
            "• [HEX]：响应格式为十六进制。\n"
            "• [+\\r\\n]：启用了回车换行。\n"
            "• [HEX +\\r\\n]：同时启用以上两项。\n\n"
            "▎规则管理\n"
            "• 选中规则后点击「编辑规则」，或双击规则进入编辑模式。\n"
            "• 上移/下移按钮调整规则的匹配优先级（从上到下依次匹配）。\n"
            "• 导出规则：弹出文件选择对话框，可自定义保存路径和文件名（.json格式）。\n"
            "• 导入规则：弹出文件选择对话框，可选择任意规则文件导入。\n"
            "  注：导入规则会覆盖当前未保存内容，请谨慎操作。\n\n"
            "▎提示\n"
            "• 规则日志显示所有匹配和响应记录，便于调试。\n"
            "• 触发条件和响应内容支持转义序列：\\r、\\n、\\t、\\\\。\n"
            "• 手动输入 help\\r\\n 和勾选回车换行效果相同，不会重复添加。\n"
            "• 规则文件格式错误时会显示具体错误信息，便于排查。"
        )
        QMessageBox.information(self, "帮助 - 自动应答", help_text)

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
                'max_count': rule['max_count'],
                'newline': rule.get('newline', False),
            })

        config = {
            'rules': rules_data,
            'global': {
                'delay': self.spin_delay.value(),
                'case_ignore': self.check_case_ignore.isChecked(),
                'stop_after_match': self.check_stop_after_match.isChecked(),
                'log_to_receive': self.check_log_to_receive.isChecked(),
            }
        }

        os.makedirs(self._last_rules_dir, exist_ok=True)
        default_path = os.path.join(self._last_rules_dir, 'auto_reply_rules.json')

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存规则文件", default_path,
            "JSON文件 (*.json);;所有文件 (*)"
        )

        if not file_path:
            return

        if not file_path.endswith('.json'):
            file_path += '.json'

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self._clean_state = self._serialize_persistent_state()
            self._last_rules_dir = os.path.dirname(file_path)
            self._append_log(f"规则已保存到: {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"保存规则失败: {e}")

    def _validate_rules_data(self, data):
        if isinstance(data, list):
            rules_data = data
        elif isinstance(data, dict):
            rules_data = data.get('rules', [])
        else:
            return False, "文件格式错误：不是有效的JSON格式"

        if not isinstance(rules_data, list):
            return False, "文件格式错误：rules字段必须是数组"

        for i, rule in enumerate(rules_data):
            if not isinstance(rule, dict):
                return False, f"第{i+1}条规则格式错误：不是对象"
            if ('trigger' not in rule or not isinstance(rule['trigger'], str)
                    or not rule['trigger'].strip()):
                return False, f"第{i+1}条规则缺少或无效的触发条件"
            if ('response' not in rule or not isinstance(rule['response'], str)
                    or not rule['response'].strip()):
                return False, f"第{i+1}条规则缺少或无效的响应内容"
            if rule.get('match_mode', '文本包含') not in ('文本包含', '文本完全', 'HEX匹配'):
                return False, f"第{i+1}条规则的匹配模式无效"
            if rule.get('response_format', '文本') not in ('文本', 'HEX'):
                return False, f"第{i+1}条规则的响应格式无效"
            if (rule.get('match_mode', '文本包含') == 'HEX匹配'
                    and not AutoReplyDialog._is_valid_hex(rule['trigger'])):
                return False, f"第{i+1}条规则的触发条件不是有效的 HEX 数据"
            if (rule.get('response_format', '文本') == 'HEX'
                    and not AutoReplyDialog._is_valid_hex(rule['response'])):
                return False, f"第{i+1}条规则的响应内容不是有效的 HEX 数据"
            if not isinstance(rule.get('enabled', True), bool):
                return False, f"第{i+1}条规则的 enabled 必须是布尔值"
            max_count = rule.get('max_count', 0)
            if not isinstance(max_count, int) or isinstance(max_count, bool) or max_count < 0:
                return False, f"第{i+1}条规则的 max_count 必须是非负整数"
            if not isinstance(rule.get('newline', False), bool):
                return False, f"第{i+1}条规则的 newline 必须是布尔值"

        return True, ""

    def _load_rules(self):
        import json
        import os

        os.makedirs(self._last_rules_dir, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self, "加载规则文件", self._last_rules_dir,
            "JSON文件 (*.json);;所有文件 (*)"
        )

        if not file_path:
            return

        if self._has_unsaved_changes():
            reply = QMessageBox.question(
                self, "确认加载",
                "导入规则将覆盖当前未保存的规则、设置或编辑草稿，是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            valid, error_msg = self._validate_rules_data(data)
            if not valid:
                QMessageBox.warning(self, "格式错误", f"规则文件格式不正确：\n{error_msg}")
                return

            if isinstance(data, list):
                rules_data = data
                global_settings = {}
            else:
                rules_data = data.get('rules', [])
                global_settings = data.get('global', {})

            new_rules = []
            for r in rules_data:
                new_rules.append({
                    'trigger': r.get('trigger', ''),
                    'match_mode': r.get('match_mode', '文本包含'),
                    'response': r.get('response', ''),
                    'response_format': r.get('response_format', '文本'),
                    'enabled': r.get('enabled', True),
                    'max_count': r.get('max_count', 0),
                    'newline': r.get('newline', False),
                    'count': 0
                })

            self._rules = new_rules

            if global_settings:
                self.spin_delay.setValue(global_settings.get('delay', 100))
                self.check_case_ignore.setChecked(global_settings.get('case_ignore', False))
                self.check_stop_after_match.setChecked(global_settings.get('stop_after_match', False))
                self.check_log_to_receive.setChecked(global_settings.get('log_to_receive', False))

            self._last_rules_dir = os.path.dirname(file_path)
            self._refresh_rules_table()
            self._clear_editor()
            self._clean_state = self._serialize_persistent_state()
            self._append_log(f"规则已加载（{len(new_rules)} 条）: {os.path.basename(file_path)}")
        except json.JSONDecodeError:
            QMessageBox.warning(self, "加载失败", "文件不是有效的JSON格式")
        except FileNotFoundError:
            QMessageBox.warning(self, "加载失败", "文件不存在")
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
                self._data_buffer = self._data_buffer[-4096:]

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
                if rule.get('newline', False):
                    response_text = response_text.rstrip('\r\n') + '\r\n'
                response_data = response_text.encode('utf-8')

            delay = self.spin_delay.value()
            timer = QTimer(self)
            timer.setSingleShot(True)
            self._response_timers.add(timer)

            def send_and_release(rd=response_data, active_timer=timer):
                try:
                    self._do_send(rd)
                finally:
                    self._response_timers.discard(active_timer)
                    active_timer.deleteLater()

            timer.timeout.connect(send_and_release)
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
                    self.parent_window.append_text(f"[发送]: {text}")
                except:
                    hex_str = ' '.join([f'{byte:02X}' for byte in data])
                    self.parent_window.append_text(f"[发送]: {hex_str}")
        except Exception as e:
            self._append_log(f"发送失败: {e}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.isVisible() and self._has_unsaved_changes():
            reply = QMessageBox.question(
                self, "未保存的更改",
                "自动应答规则、设置或编辑草稿尚未导出，确定关闭吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        self._closing = True
        self._connection_timer.stop()
        if hasattr(self.parent_window, '_auto_reply_dialog'):
            self.parent_window._auto_reply_dialog = None
        event.accept()
