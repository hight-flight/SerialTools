"""
独立工具对话框：CRC 计算器、HEX 转换器、串口监视器、使用说明、关于。
所有对话框均独立于 SerialTool 的内部状态，仅需 parent 和主题信息。
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QPushButton, QLineEdit,
                             QMessageBox, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView, QFrame, QFormLayout)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from theme import apply_dialog_theme

# --- 全局常量 ---
VERSION = "1.2.4"


# ═══════════════════════════════════════════════════════════════
#  CRC 计算器
# ═══════════════════════════════════════════════════════════════

def show_crc_calculator(parent, is_dark=False):
    """CRC 计算器弹窗"""
    dialog = QDialog(parent)
    dialog.setWindowTitle("CRC 计算器")
    dialog.setMinimumSize(420, 280)
    dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)

    layout = QVBoxLayout(dialog)
    layout.setSpacing(8)

    # 输入区
    input_layout = QHBoxLayout()
    input_layout.addWidget(QLabel("数据 (HEX):"))
    input_edit = QLineEdit()
    input_edit.setFont(QFont("Consolas", 9))
    input_edit.setPlaceholderText("例如: 0103 或 010304")
    input_layout.addWidget(input_edit)
    layout.addLayout(input_layout)

    # CRC 类型
    type_layout = QHBoxLayout()
    type_layout.addWidget(QLabel("算法:"))
    combo_algo = QComboBox()
    combo_algo.addItems(["Modbus CRC16", "CRC32", "Fletcher", "XOR8", "ADD8", "ADD16"])
    combo_algo.setFont(QFont("Consolas", 9))
    type_layout.addWidget(combo_algo)
    type_layout.addStretch()
    layout.addLayout(type_layout)

    # 结果显示
    result_label = QLabel("结果: —")
    result_label.setFont(QFont("Consolas", 11, QFont.Bold))
    result_label.setFrameStyle(QFrame.Panel | QFrame.Sunken)
    result_label.setTextFormat(Qt.RichText)
    result_label.setMinimumHeight(36)
    result_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(result_label)

    layout.addStretch()

    # 按钮
    btn_layout = QHBoxLayout()
    btn_layout.addStretch()

    def on_calc():
        hex_str = input_edit.text().strip().replace(' ', '')
        if not hex_str:
            result_label.setText("结果: 请输入数据")
            return
        try:
            data = bytes.fromhex(hex_str)
        except ValueError:
            result_label.setText("结果: HEX 格式错误")
            return

        algo = combo_algo.currentText()
        if algo == "Modbus CRC16":
            crc = 0xFFFF
            for b in data:
                crc ^= b
                for _ in range(8):
                    if crc & 1:
                        crc = (crc >> 1) ^ 0xA001
                    else:
                        crc >>= 1
            result_label.setText(f"结果: 0x{crc:04X} ({crc})")
        elif algo == "CRC32":
            import zlib
            crc = zlib.crc32(data) & 0xFFFFFFFF
            result_label.setText(f"结果: 0x{crc:08X} ({crc})")
        elif algo == "Fletcher":
            sum1, sum2 = 0, 0
            for b in data:
                sum1 = (sum1 + b) % 255
                sum2 = (sum2 + sum1) % 255
            crc = (sum2 << 8) | sum1
            result_label.setText(f"结果: 0x{crc:04X} ({crc})")
        elif algo == "XOR8":
            x = 0
            for b in data:
                x ^= b
            result_label.setText(f"结果: 0x{x:02X} ({x})")
        elif algo == "ADD8":
            s = sum(data) & 0xFF
            result_label.setText(f"结果: 0x{s:02X} ({s})")
        elif algo == "ADD16":
            s = sum(data) & 0xFFFF
            result_label.setText(f"结果: 0x{s:04X} ({s})")

    calc_btn = QPushButton("计算")
    calc_btn.setMinimumWidth(72)
    calc_btn.clicked.connect(on_calc)
    btn_layout.addWidget(calc_btn)

    close_btn = QPushButton("关闭")
    close_btn.clicked.connect(dialog.close)
    btn_layout.addWidget(close_btn)

    layout.addLayout(btn_layout)
    apply_dialog_theme(dialog, is_dark)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.show()


# ═══════════════════════════════════════════════════════════════
#  HEX 转换器
# ═══════════════════════════════════════════════════════════════

def show_hex_converter(parent, is_dark=False):
    """HEX 转换器弹窗：HEX ↔ ASCII ↔ Decimal 互转"""
    dialog = QDialog(parent)
    dialog.setWindowTitle("HEX 转换器")
    dialog.setMinimumSize(480, 320)
    dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)

    layout = QFormLayout(dialog)
    layout.setSpacing(8)

    hex_edit = QLineEdit()
    hex_edit.setFont(QFont("Consolas", 9))
    hex_edit.setPlaceholderText("输入 HEX，如 48 65 6C 6C 6F")
    layout.addRow("HEX:", hex_edit)

    ascii_edit = QLineEdit()
    ascii_edit.setFont(QFont("Consolas", 9))
    ascii_edit.setPlaceholderText("输入 ASCII 文本")
    layout.addRow("ASCII:", ascii_edit)

    dec_edit = QLineEdit()
    dec_edit.setFont(QFont("Consolas", 9))
    dec_edit.setPlaceholderText("输入十进制（空格分隔或单个数值）")
    layout.addRow("Decimal:", dec_edit)

    # 实时转换：HEX → ASCII + Decimal
    def on_hex_changed(text):
        text = text.strip().replace(' ', '')
        if not text:
            return
        try:
            data = bytes.fromhex(text)
            ascii_edit.blockSignals(True)
            ascii_edit.setText(data.decode('ascii', errors='replace'))
            ascii_edit.blockSignals(False)
            dec_edit.blockSignals(True)
            dec_edit.setText(' '.join(str(b) for b in data))
            dec_edit.blockSignals(False)
        except (ValueError, UnicodeDecodeError):
            pass

    def on_ascii_changed(text):
        if not text:
            return
        data = text.encode('ascii', errors='replace')
        hex_edit.blockSignals(True)
        hex_edit.setText(data.hex(' ').upper())
        hex_edit.blockSignals(False)
        dec_edit.blockSignals(True)
        dec_edit.setText(' '.join(str(b) for b in data))
        dec_edit.blockSignals(False)

    hex_edit.textEdited.connect(on_hex_changed)
    ascii_edit.textEdited.connect(on_ascii_changed)

    apply_dialog_theme(dialog, is_dark)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.show()


# ═══════════════════════════════════════════════════════════════
#  串口监视器
# ═══════════════════════════════════════════════════════════════

def show_serial_monitor(parent, is_dark=False):
    """串口监视器：列出系统所有串口及详细信息"""
    dialog = QDialog(parent)
    dialog.setWindowTitle("串口监视器")
    dialog.setMinimumSize(600, 350)
    dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)

    layout = QVBoxLayout(dialog)
    layout.setSpacing(8)

    table = QTableWidget()
    table.setColumnCount(6)
    table.setHorizontalHeaderLabels(["序号", "端口", "描述", "硬件ID", "制造商", "VID/PID"])
    table.setColumnWidth(0, 44)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    layout.addWidget(table)

    def refresh():
        table.setRowCount(0)
        try:
            from serial.tools import list_ports
            ports = list_ports.comports()
            table.setRowCount(len(ports))
            for i, p in enumerate(ports):
                table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
                table.setItem(i, 1, QTableWidgetItem(p.device))
                table.setItem(i, 2, QTableWidgetItem(p.description))
                table.setItem(i, 3, QTableWidgetItem(p.hwid))
                table.setItem(i, 4, QTableWidgetItem(p.manufacturer or "—"))
                vid_pid = f"{p.vid:04X}:{p.pid:04X}" if p.vid and p.pid else "—"
                table.setItem(i, 5, QTableWidgetItem(vid_pid))
        except Exception as e:
            QMessageBox.warning(dialog, "错误", f"枚举串口失败: {e}")

    # 按钮行
    btn_layout = QHBoxLayout()
    btn_refresh_monitor = QPushButton("刷新")
    btn_refresh_monitor.setMinimumWidth(72)
    btn_refresh_monitor.clicked.connect(refresh)
    btn_layout.addWidget(btn_refresh_monitor)
    btn_layout.addStretch()
    btn_close = QPushButton("关闭")
    btn_close.clicked.connect(dialog.close)
    btn_layout.addWidget(btn_close)
    layout.addLayout(btn_layout)

    refresh()
    apply_dialog_theme(dialog, is_dark)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.show()


# ═══════════════════════════════════════════════════════════════
#  使用说明
# ═══════════════════════════════════════════════════════════════

def show_usage_dialog(parent, is_dark=False):
    """显示使用说明"""
    msg = QMessageBox(QMessageBox.Information, "使用说明",
        "<b>hight-flight 串口调试助手</b> v" + VERSION + "<br><br>"

        "<b>━━ 主界面 ━━</b><br>"
        "• 菜单栏：文件(保存/导出) | 视图(主题/显示) | 工具 | 帮助<br>"
        "• 切换暗黑/明亮模式：视图 → 暗黑模式<br>"
        "• 清空接收区会弹出确认提示<br><br>"

        "<b>━━ 串口连接 ━━</b><br>"
        "1. 选择连接参数，点击「打开连接」<br>"
        "2. 波特率选「自定义」可输入任意值 (1～1000000)<br>"
        "3. 「更多串口设置」可配数据位/停止位/校验位/流控制<br>"
        "4. RTS/DTR 勾选后立即生效<br><br>"

        "<b>━━ 发送数据 ━━</b><br>"
        "• 在发送区输入内容，点击「发送」(Ctrl+Return)<br>"
        "• HEX 发送：勾选后输入十六进制字符串，空格随意<br>"
        "• 回车换行：勾选后发送内容末尾自动追加 \\r\\n<br>"
        "• 重复发送：勾选后按间隔(ms)自动循环发送<br>"
        "• 校验：选算法后自动计算校验值并追加到帧尾<br>"
        "• 首/尾字段：勾选后自动在数据前后添加指定字节<br>"
        "• 文件发送：选择文件→「发送文件」→ 通过串口发出<br><br>"

        "<b>━━ 接收与显示 ━━</b><br>"
        "• 接收区自动显示串口返回的数据<br>"
        "• HEX 显示：勾选后数据以十六进制格式显示<br>"
        "• 显示时间：勾选后每条数据前追加时间戳<br>"
        "• 筛选：支持包含/忽略大小写/正则，逗号分隔多关键字<br>"
        "• 编码：支持 UTF-8/GBK/GB2312/ASCII/ISO-8859-1/GB18030<br>"
        "• 自动保存：勾选后全部接收数据持久化到日志文件<br><br>"

        "<b>━━ 多字符发送 ━━</b><br>"
        "• 可预设最多 N 条指令，每条独立设置 HEX/字符串/延时/顺序<br>"
        "• 点击「发送」发送指令，快速三击按钮可编辑按钮文字<br>"
        "• 循环发送：按顺序从小到大依次发送，可限循环次数<br>"
        "• 支持保存/加载配置到文件<br><br>"

        "<b>━━ 工具 ━━</b><br>"
        "<b>CRC 计算器：</b>输入 HEX 数据，选算法即得校验值<br>"
        "  支持 Modbus CRC16 / CRC32 / Fletcher / XOR8 / ADD8 / ADD16<br>"
        "<b>HEX 转换器：</b>HEX ↔ ASCII ↔ Decimal 实时互转<br>"
        "<b>串口监视器：</b>列出系统所有串口的端口/描述/硬件ID/制造商<br>"
        "<b>Modbus 工具：</b><br>"
        "  帧构建 — 选功能码+填参数→生成 RTU/ASCII/TCP 帧<br>"
        "  帧解析 — 粘贴 Modbus 帧→自动解析字段与 CRC 校验<br>"
        "<b>数据波形（示波器）：</b><br>"
        "  将串口接收的原始字节按数据类型解析为实时波形<br>"
        "  通道识别：N 通道 × M 字节/值 = 帧长，按帧循环取<br>"
        "  例 — 2 通道 + uint8：AA BB CC DD → CH1:AA,CC CH2:BB,DD<br>"
        "  例 — 2 通道 + uint16_be：00 64 00 C8 → CH1:100 CH2:200<br>"
        "  单片机按固定帧格式连续发送 ADC 采样值即可显示波形<br><br>"

        "<b>━━ 快捷键 ━━</b><br>"
        "  Ctrl+Return — 发送数据<br>"
        "  Ctrl+S — 保存接收日志"
    )
    apply_dialog_theme(msg, is_dark)
    msg.exec_()


# ═══════════════════════════════════════════════════════════════
#  关于
# ═══════════════════════════════════════════════════════════════

def show_about_dialog(parent, is_dark=False):
    """显示关于对话框"""
    msg = QMessageBox(QMessageBox.NoIcon, "关于",
        f"<b>hight-flight 串口调试助手</b><br>"
        f"版本: {VERSION}<br><br>"
        "基于 PyQt5 的跨平台串口调试工具。<br>"
        "支持 Modbus CRC、文件发送、多字符批量发送、<br>"
        "亮色/暗黑双主题、ANSI 转义码解析等功能。<br><br>"
        "<b>作者:</b> GAOXIANG<br>"
        "<b>联系方式:</b> 770807059@qq.com"
    )
    apply_dialog_theme(msg, is_dark)
    msg.exec_()
