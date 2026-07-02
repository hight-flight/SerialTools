"""
GSM/Modem AT指令调试助手

功能特点：
- 常用AT指令快捷按钮（厂家标识、模块标识、IMEI、ICCID、本机号码、版本等）
- 指令终端：手动输入和发送任意AT指令
- 响应区域：显示指令响应结果
- 短信管理：读取、发送、删除短信
- 通话控制：拨打、接听、挂断电话
- 状态监控：信号强度、SIM状态、网络注册状态
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QPushButton, QLineEdit, QTextEdit,
                             QMessageBox, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView, QGroupBox,
                             QFrame, QSizePolicy, QCheckBox, QTabWidget,
                             QWidget)
from PyQt5.QtCore import Qt, QTimer, QMutexLocker
from PyQt5.QtGui import QFont, QTextCursor, QColor

from theme import apply_dialog_theme, DataReceiver


class GSMDebuggerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self._data_buffer = b''
        self._data_receiver = DataReceiver()
        self._data_receiver.data_received.connect(self._process_receive_data)
        self._init_ui()
        self.setAttribute(Qt.WA_DeleteOnClose)

    def _init_ui(self):
        self.setWindowTitle("GSM 调试助手")
        self.setMinimumSize(720, 620)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── 标签页：按功能域分组，降低单屏按钮密度 ──
        self.tab_widget = QTabWidget()

        # 标签页 1：AT 指令终端
        tab_terminal = QWidget()
        terminal_layout = QVBoxLayout(tab_terminal)
        terminal_layout.setSpacing(6)
        terminal_layout.setContentsMargins(4, 4, 4, 4)
        self._build_status_bar(terminal_layout)
        self._build_info_buttons(terminal_layout)
        self._build_control_buttons(terminal_layout)
        self._build_command_input(terminal_layout)
        terminal_layout.addStretch()
        self.tab_widget.addTab(tab_terminal, "AT 终端")

        # 标签页 2：短信管理
        tab_sms = QWidget()
        sms_tab_layout = QVBoxLayout(tab_sms)
        sms_tab_layout.setSpacing(6)
        sms_tab_layout.setContentsMargins(4, 4, 4, 4)
        self._build_sms_section(sms_tab_layout)
        self.tab_widget.addTab(tab_sms, "短信管理")

        # 标签页 3：通话控制
        tab_call = QWidget()
        call_tab_layout = QVBoxLayout(tab_call)
        call_tab_layout.setSpacing(6)
        call_tab_layout.setContentsMargins(4, 4, 4, 4)
        self._build_call_section(call_tab_layout)
        call_tab_layout.addStretch()
        self.tab_widget.addTab(tab_call, "通话控制")

        layout.addWidget(self.tab_widget)

        # ── 响应区（全局可见，任何标签页的操作结果都显示在这里）──
        self._build_response_area(layout)

        # ── 底部选项栏 ──
        self._build_options_bar(layout)

    def _build_status_bar(self, parent_layout):
        status_group = QGroupBox("模块状态")
        status_layout = QHBoxLayout(status_group)
        status_layout.setSpacing(8)

        status_layout.addWidget(QLabel("状态指示:"))

        self.lbl_signal = QLabel("信号强度: —")
        self.lbl_signal.setFont(QFont("Consolas", 9))
        self.lbl_signal.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.lbl_signal.setMinimumWidth(120)
        self.lbl_signal.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.lbl_signal)

        self.lbl_sim = QLabel("SIM状态: —")
        self.lbl_sim.setFont(QFont("Consolas", 9))
        self.lbl_sim.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.lbl_sim.setMinimumWidth(100)
        self.lbl_sim.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.lbl_sim)

        self.lbl_network = QLabel("网络状态: —")
        self.lbl_network.setFont(QFont("Consolas", 9))
        self.lbl_network.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.lbl_network.setMinimumWidth(120)
        self.lbl_network.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.lbl_network)

        self.lbl_operator = QLabel("运营商: —")
        self.lbl_operator.setFont(QFont("Consolas", 9))
        self.lbl_operator.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.lbl_operator.setMinimumWidth(120)
        self.lbl_operator.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.lbl_operator)

        btn_refresh = QPushButton("刷新状态")
        btn_refresh.setMinimumWidth(80)
        btn_refresh.clicked.connect(self._refresh_status)
        status_layout.addWidget(btn_refresh)

        status_layout.addStretch()
        parent_layout.addWidget(status_group)

    def _build_info_buttons(self, parent_layout):
        info_group = QGroupBox("设备信息查询")
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(4)

        info_row1 = [
            ("厂家标识", "AT+CGMI"),
            ("模块标识", "AT+CGMM"),
            ("模块IMEI", "AT+CGSN"),
            ("SIM卡ICCID", "AT+ICCID"),
            ("本机号码", "AT+CNUM"),
        ]

        info_row2 = [
            ("模块版本", "AT+CGMR"),
            ("运营商", "AT+COPS?"),
            ("信号强度", "AT+CSQ"),
            ("网络注册", "AT+CREG?"),
            ("SIM状态", "AT+CPIN?"),
        ]

        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(4)
        for label, cmd in info_row1:
            btn = QPushButton(label)
            btn.setMinimumWidth(85)
            btn.setToolTip(cmd)
            btn.clicked.connect(lambda checked, c=cmd: self._send_command(c))
            row1_layout.addWidget(btn)
        row1_layout.addStretch()
        info_layout.addLayout(row1_layout)

        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(4)
        for label, cmd in info_row2:
            btn = QPushButton(label)
            btn.setMinimumWidth(85)
            btn.setToolTip(cmd)
            btn.clicked.connect(lambda checked, c=cmd: self._send_command(c))
            row2_layout.addWidget(btn)
        row2_layout.addStretch()
        info_layout.addLayout(row2_layout)

        parent_layout.addWidget(info_group)

    def _build_control_buttons(self, parent_layout):
        control_group = QGroupBox("控制指令")
        control_layout = QVBoxLayout(control_group)
        control_layout.setSpacing(4)

        control_row1 = [
            ("重启模块", "AT+CFUN=1,1"),
            ("恢复出厂", "AT&F"),
            ("挂断电话", "ATH"),
            ("查询网络模式", "AT+COPS=?"),
            ("查询短信存储", "AT+CPMS?"),
        ]

        control_row2 = [
            ("设置文本", "AT+CMGF=1"),
            ("设置PDU", "AT+CMGF=0"),
            ("列出短信", "AT+CMGL=\"ALL\""),
            ("开启回显", "ATE1"),
            ("关闭回显", "ATE0"),
        ]
        # 危险操作单独一行，使用统一样式（主题 QSS 处理）
        control_row3 = [
            ("清空短信", "AT+CMGD=1,4"),
        ]

        dangerous_commands = {"重启模块", "恢复出厂", "清空短信"}
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(4)
        for label, cmd in control_row1:
            btn = QPushButton(label)
            btn.setMinimumWidth(90)
            btn.setToolTip(cmd)
            if label in dangerous_commands:
                btn.clicked.connect(lambda checked, c=cmd, l=label: self._send_dangerous_command(c, l))
            else:
                btn.clicked.connect(lambda checked, c=cmd: self._send_command(c))
            row1_layout.addWidget(btn)
        row1_layout.addStretch()
        control_layout.addLayout(row1_layout)

        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(4)
        for label, cmd in control_row2:
            btn = QPushButton(label)
            btn.setMinimumWidth(80)
            btn.setToolTip(cmd)
            btn.clicked.connect(lambda checked, c=cmd: self._send_command(c))
            row2_layout.addWidget(btn)
        row2_layout.addStretch()
        control_layout.addLayout(row2_layout)

        # 危险操作行
        row3_layout = QHBoxLayout()
        row3_layout.setSpacing(4)
        for label, cmd in control_row3:
            btn = QPushButton(label)
            btn.setMinimumWidth(90)
            btn.setToolTip(cmd + " (危险操作)")
            btn.clicked.connect(lambda checked, c=cmd: self._send_dangerous_command(c, label))
            row3_layout.addWidget(btn)
        row3_layout.addStretch()
        control_layout.addLayout(row3_layout)

        parent_layout.addWidget(control_group)

    def _build_command_input(self, parent_layout):
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)

        self.edit_command = QLineEdit()
        self.edit_command.setFont(QFont("Consolas", 10))
        self.edit_command.setPlaceholderText("输入AT指令，如: AT+CSQ")
        self.edit_command.returnPressed.connect(self._send_command_from_input)
        input_layout.addWidget(self.edit_command)

        btn_send = QPushButton("发送")
        btn_send.setMinimumWidth(72)
        btn_send.clicked.connect(self._send_command_from_input)
        input_layout.addWidget(btn_send)

        btn_clear_input = QPushButton("清空")
        btn_clear_input.setMinimumWidth(72)
        btn_clear_input.clicked.connect(self._clear_command_input)
        input_layout.addWidget(btn_clear_input)

        parent_layout.addLayout(input_layout)

    def _build_response_area(self, parent_layout):
        response_group = QGroupBox("指令响应")
        response_layout = QVBoxLayout(response_group)
        response_layout.setSpacing(4)

        self.text_response = QTextEdit()
        self.text_response.setFont(QFont("Consolas", 9))
        self.text_response.setReadOnly(True)
        self.text_response.setMinimumHeight(120)
        response_layout.addWidget(self.text_response)

        parent_layout.addWidget(response_group)

    def _build_sms_section(self, parent_layout):
        sms_group = QGroupBox("短信管理")
        sms_layout = QVBoxLayout(sms_group)
        sms_layout.setSpacing(6)

        sms_input_layout = QHBoxLayout()
        sms_input_layout.setSpacing(8)

        self.edit_sms_phone = QLineEdit()
        self.edit_sms_phone.setFont(QFont("Consolas", 9))
        self.edit_sms_phone.setPlaceholderText("号码")
        self.edit_sms_phone.setMaximumWidth(120)
        sms_input_layout.addWidget(self.edit_sms_phone)

        self.edit_sms_content = QLineEdit()
        self.edit_sms_content.setFont(QFont("Consolas", 9))
        self.edit_sms_content.setPlaceholderText("内容")
        sms_input_layout.addWidget(self.edit_sms_content)

        btn_send_sms = QPushButton("发送短信")
        btn_send_sms.setMinimumWidth(80)
        btn_send_sms.clicked.connect(self._send_sms)
        sms_input_layout.addWidget(btn_send_sms)

        btn_read_sms = QPushButton("读取短信")
        btn_read_sms.setMinimumWidth(80)
        btn_read_sms.clicked.connect(self._read_sms)
        sms_input_layout.addWidget(btn_read_sms)

        sms_input_layout.addStretch()
        sms_layout.addLayout(sms_input_layout)

        self.table_sms = QTableWidget()
        self.table_sms.setColumnCount(5)
        self.table_sms.setHorizontalHeaderLabels(["序号", "号码", "时间", "状态", "内容"])
        self.table_sms.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_sms.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_sms.setMinimumHeight(80)
        self._show_sms_empty_state()
        sms_layout.addWidget(self.table_sms)

        sms_btn_layout = QHBoxLayout()
        sms_btn_layout.addStretch()

        btn_delete_sms = QPushButton("删除选中")
        btn_delete_sms.clicked.connect(self._delete_selected_sms)
        sms_btn_layout.addWidget(btn_delete_sms)

        btn_clear_sms = QPushButton("清空列表")
        btn_clear_sms.clicked.connect(self._clear_sms_list)
        sms_btn_layout.addWidget(btn_clear_sms)

        sms_layout.addLayout(sms_btn_layout)
        parent_layout.addWidget(sms_group)

    def _build_call_section(self, parent_layout):
        call_group = QGroupBox("通话控制")
        call_layout = QHBoxLayout(call_group)
        call_layout.setSpacing(8)

        self.edit_call_num = QLineEdit()
        self.edit_call_num.setFont(QFont("Consolas", 9))
        self.edit_call_num.setPlaceholderText("号码")
        self.edit_call_num.setMaximumWidth(120)
        call_layout.addWidget(self.edit_call_num)

        btn_call = QPushButton("拨打电话")
        btn_call.setMinimumWidth(80)
        btn_call.clicked.connect(self._make_call)
        call_layout.addWidget(btn_call)

        btn_hangup = QPushButton("挂断电话")
        btn_hangup.setMinimumWidth(80)
        btn_hangup.clicked.connect(lambda: self._send_command("ATH"))
        call_layout.addWidget(btn_hangup)

        btn_answer = QPushButton("接听电话")
        btn_answer.setMinimumWidth(80)
        btn_answer.clicked.connect(lambda: self._send_command("ATA"))
        call_layout.addWidget(btn_answer)

        call_layout.addStretch()
        parent_layout.addWidget(call_group)

    def _build_options_bar(self, parent_layout):
        options_layout = QHBoxLayout()
        options_layout.setSpacing(16)

        self.check_newline = QCheckBox("自动添加换行")
        self.check_newline.setChecked(True)
        options_layout.addWidget(self.check_newline)

        options_layout.addStretch()

        btn_clear_response = QPushButton("清空响应")
        btn_clear_response.setMinimumWidth(80)
        btn_clear_response.clicked.connect(self._clear_response)
        options_layout.addWidget(btn_clear_response)

        btn_close = QPushButton("关闭")
        btn_close.setMinimumWidth(72)
        btn_close.clicked.connect(self.close)
        options_layout.addWidget(btn_close)

        parent_layout.addLayout(options_layout)

    def _send_dangerous_command(self, command, label):
        """发送危险指令前弹确认框。"""
        reply = QMessageBox.question(
            self, "确认操作",
            f"确定要执行「{label}」吗？\n指令: {command}\n此操作可能不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._send_command(command)

    def _send_command(self, command):
        if not hasattr(self.parent_window, 'transport') or not self.parent_window.transport or not self.parent_window.transport.is_open:
            QMessageBox.warning(self, "连接错误", "请先打开串口连接")
            return

        self.edit_command.setText(command)

        if self.check_newline.isChecked():
            cmd_to_send = command + "\r\n"
        else:
            cmd_to_send = command

        data = cmd_to_send.encode('utf-8')

        try:
            with QMutexLocker(self.parent_window.serial_mutex):
                self.parent_window.transport.write(data)
            self._append_sent_command(f">>> {command}")
        except Exception as e:
            QMessageBox.warning(self, "发送失败", f"发送命令失败: {e}")

    def _send_command_from_input(self):
        command = self.edit_command.text().strip()
        if not command:
            QMessageBox.warning(self, "输入错误", "请输入AT指令")
            return
        self._send_command(command)

    def _send_sms(self):
        phone = self.edit_sms_phone.text().strip()
        content = self.edit_sms_content.text().strip()

        if not phone:
            QMessageBox.warning(self, "输入错误", "请输入收件人号码")
            return
        if not content:
            QMessageBox.warning(self, "输入错误", "请输入短信内容")
            return

        self._send_command('AT+CMGF=1')
        QTimer.singleShot(300, lambda: self._send_sms_content(phone, content))

    def _send_sms_content(self, phone, content):
        cmd = f'AT+CMGS="{phone}"'
        self._send_command(cmd)
        QTimer.singleShot(500, lambda: self._send_sms_data(content))

    def _send_sms_data(self, content):
        if not hasattr(self.parent_window, 'transport') or not self.parent_window.transport or not self.parent_window.transport.is_open:
            QMessageBox.warning(self, "连接错误", "串口连接已断开")
            return
        data = content.encode('utf-8') + b'\x1A'
        try:
            with QMutexLocker(self.parent_window.serial_mutex):
                self.parent_window.transport.write(data)
            self._append_sent_command(f">>> {content} (Ctrl+Z)")
        except Exception as e:
            QMessageBox.warning(self, "发送失败", f"发送短信失败: {e}")

    def _read_sms(self):
        self._clear_sms_list()
        self._send_command('AT+CMGF=1')
        QTimer.singleShot(300, lambda: self._send_command('AT+CMGL="ALL"'))

    def _delete_selected_sms(self):
        row = self.table_sms.currentRow()
        if row < 0:
            QMessageBox.warning(self, "操作错误", "请先选中要删除的短信")
            return

        index_item = self.table_sms.item(row, 0)
        if not index_item:
            return
        sms_index = index_item.text()
        # 跳过空状态占位行
        if sms_index.startswith("暂无"):
            return

        phone = self.table_sms.item(row, 1).text() if self.table_sms.item(row, 1) else ""
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要从模块中删除短信 #{sms_index}（{phone}）吗？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._send_command(f"AT+CMGD={sms_index}")
        self.table_sms.removeRow(row)

        # 删除后若表为空则恢复空状态提示
        if self.table_sms.rowCount() == 0:
            self._show_sms_empty_state()

    def _clear_sms_list(self):
        self.table_sms.setRowCount(0)
        self._show_sms_empty_state()

    def _show_sms_empty_state(self):
        """在 SMS 表格中显示空状态提示。"""
        self.table_sms.setRowCount(1)
        empty_item = QTableWidgetItem("暂无短信，请点击「读取短信」获取")
        empty_item.setForeground(Qt.gray)
        empty_item.setTextAlignment(Qt.AlignCenter)
        self.table_sms.setItem(0, 0, empty_item)
        self.table_sms.setSpan(0, 0, 1, 5)

    def _make_call(self):
        phone = self.edit_call_num.text().strip()
        if not phone:
            QMessageBox.warning(self, "输入错误", "请输入电话号码")
            return

        self._send_command(f"ATD{phone};")

    def _refresh_status(self):
        self._send_command("AT+CSQ")
        QTimer.singleShot(200, lambda: self._send_command("AT+CPIN?"))
        QTimer.singleShot(400, lambda: self._send_command("AT+CREG?"))
        QTimer.singleShot(600, lambda: self._send_command("AT+COPS?"))

    def _clear_command_input(self):
        """清空命令输入框。"""
        self.edit_command.clear()

    def _clear_response(self):
        """清空指令响应区域。"""
        self.text_response.clear()

    def _append_sent_command(self, text):
        """以区分色显示已发送的命令。"""
        is_dark = getattr(self.parent_window, 'current_theme', 'light') == 'dark'
        old_color = self.text_response.textColor()
        self.text_response.setTextColor(QColor("#529BFF") if is_dark else QColor("#005A9E"))
        self.text_response.append(text)
        self.text_response.setTextColor(old_color)
        cursor = self.text_response.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.text_response.setTextCursor(cursor)

    def _append_response(self, text):
        self.text_response.append(text)
        cursor = self.text_response.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.text_response.setTextCursor(cursor)

    def handle_receive_data(self, data):
        self._data_receiver.data_received.emit(data)

    def _process_receive_data(self, data):
        self._data_buffer += data

        try:
            encoding = getattr(self.parent_window, 'combo_encoding', None)
            if encoding:
                encoding = encoding.currentText()
            else:
                encoding = 'UTF-8'

            # 使用 bytes 分割避免编解码往返损坏数据
            if b'\n' in self._data_buffer:
                byte_lines = self._data_buffer.split(b'\n')
                self._data_buffer = byte_lines[-1]  # 保留不完整的尾部

                for byte_line in byte_lines[:-1]:
                    try:
                        line = byte_line.decode(encoding, errors='replace').replace('\r', '')
                    except LookupError:
                        line = byte_line.decode('utf-8', errors='replace').replace('\r', '')
                    if line:
                        self._append_response(line)
                        self._parse_response(line)
            elif len(self._data_buffer) > 4096:
                # 缓冲区溢出时强制刷新
                try:
                    text = self._data_buffer.decode(encoding, errors='replace')
                except LookupError:
                    text = self._data_buffer.decode('utf-8', errors='replace')
                self._append_response(text)
                self._parse_response(text)
                self._data_buffer = b''
        except Exception:
            hex_str = ' '.join([f'{byte:02X}' for byte in self._data_buffer])
            self._append_response(hex_str)
            self._data_buffer = b''

    def _parse_response(self, line):
        line = line.strip()
        
        if line.startswith('+CSQ:'):
            try:
                parts = line.split(':')[1].strip().split(',')
                rssi = int(parts[0].strip())
                ber = int(parts[1].strip()) if len(parts) > 1 else 99
                
                if rssi == 99:
                    signal_text = "信号强度: 未检测"
                elif rssi == 0:
                    signal_text = "信号强度: -113 dBm (极差)"
                elif rssi <= 10:
                    signal_text = f"信号强度: -{113 - rssi*2} dBm (差)"
                elif rssi <= 15:
                    signal_text = f"信号强度: -{113 - rssi*2} dBm (一般)"
                elif rssi <= 20:
                    signal_text = f"信号强度: -{113 - rssi*2} dBm (良好)"
                elif rssi <= 31:
                    signal_text = f"信号强度: -{113 - rssi*2} dBm (优秀)"
                else:
                    signal_text = f"信号强度: {rssi}"
                
                self.lbl_signal.setText(signal_text)
            except:
                pass
        
        elif line.startswith('+CPIN:'):
            try:
                status = line.split(':')[1].strip().strip('"')
                if status == "READY":
                    self.lbl_sim.setText("SIM状态: 已就绪")
                elif status == "PIN":
                    self.lbl_sim.setText("SIM状态: 需要PIN")
                elif status == "PUK":
                    self.lbl_sim.setText("SIM状态: 需要PUK")
                elif status == "PH-NET PIN":
                    self.lbl_sim.setText("SIM状态: 网络PIN")
                else:
                    self.lbl_sim.setText(f"SIM状态: {status}")
            except:
                pass
        
        elif line.startswith('+CREG:'):
            try:
                parts = line.split(':')[1].strip().split(',')
                n = int(parts[0].strip())
                stat = int(parts[1].strip()) if len(parts) > 1 else 0
                
                if stat == 0:
                    self.lbl_network.setText("网络状态: 未注册")
                elif stat == 1:
                    self.lbl_network.setText("网络状态: 已注册")
                elif stat == 2:
                    self.lbl_network.setText("网络状态: 搜索中")
                elif stat == 3:
                    self.lbl_network.setText("网络状态: 拒绝")
                elif stat == 4:
                    self.lbl_network.setText("网络状态: 未知")
                elif stat == 5:
                    self.lbl_network.setText("网络状态: 已注册(漫游)")
                else:
                    self.lbl_network.setText(f"网络状态: {stat}")
            except:
                pass
        
        elif line.startswith('+COPS:'):
            try:
                parts = line.split(':')[1].strip().split(',')
                operator = ""
                for part in parts:
                    part = part.strip().strip('"')
                    if part and part not in ['0', '1', '2', '3', '4']:
                        operator = part
                        break
                
                if operator:
                    self.lbl_operator.setText(f"运营商: {operator}")
                else:
                    self.lbl_operator.setText("运营商: 未知")
            except:
                pass
        
        elif line.startswith('+CMGL:'):
            try:
                parts = line.split(':')[1].strip().split(',')
                if len(parts) >= 5:
                    sms_index = parts[0].strip().strip('"')
                    stat_code = parts[1].strip().strip('"')
                    phone = parts[2].strip().strip('"')
                    date = parts[3].strip().strip('"') + ' ' + parts[4].strip().strip('"')
                    content = parts[5].strip().strip('"') if len(parts) > 5 else ""

                    # 状态码转可读文字
                    status_map = {
                        "REC UNREAD": "未读",
                        "REC READ": "已读",
                        "STO UNSENT": "待发",
                        "STO SENT": "已发",
                        "ALL": "全部",
                    }
                    status_text = status_map.get(stat_code, stat_code)

                    # 第一条数据时清除空状态行
                    if self.table_sms.rowCount() == 1:
                        first_item = self.table_sms.item(0, 0)
                        if first_item and first_item.text().startswith("暂无"):
                            self.table_sms.setRowCount(0)

                    row = self.table_sms.rowCount()
                    self.table_sms.insertRow(row)
                    self.table_sms.setItem(row, 0, QTableWidgetItem(sms_index))
                    self.table_sms.setItem(row, 1, QTableWidgetItem(phone))
                    self.table_sms.setItem(row, 2, QTableWidgetItem(date))
                    self.table_sms.setItem(row, 3, QTableWidgetItem(status_text))
                    self.table_sms.setItem(row, 4, QTableWidgetItem(content))
            except:
                pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if hasattr(self.parent_window, '_gsm_dialog'):
            self.parent_window._gsm_dialog = None
        event.accept()