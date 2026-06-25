import sys
import os
import datetime
import struct
import socket
import shutil
import json
import re
import time
import serial
import serial.tools.list_ports
from collections import deque
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QComboBox, QPushButton,
                             QTextEdit, QCheckBox, QMessageBox, QSplitter, QSpinBox, QLineEdit, QGroupBox, QDialog, QFormLayout,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QInputDialog, QSizePolicy,
                             QAction, QTabWidget, QRadioButton, QButtonGroup, QStackedWidget)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QRunnable, QThreadPool, QObject, QMetaObject, Q_ARG, pyqtSlot, QMutex, QMutexLocker, QPoint, QEvent
from PyQt5.QtGui import QFont, QTextCursor, QTextCharFormat, QColor, QPalette, QPixmap, QPainter, QPolygon

from dialogs import (show_crc_calculator, show_hex_converter,
                         show_serial_monitor, show_usage_dialog,
                         show_about_dialog)
from theme import THEME_COLORS, DARK_QSS, LIGHT_QSS, apply_dialog_theme, VERSION
from transport import TransportWrapper, TransportReadThread
# 注意：JsonViewerDialog 和 OTAControlCenter 改为延迟导入（懒加载），
# 避免启动时加载 pyqtgraph/numpy/http.server 等重型依赖，显著加快 exe 首次启动速度。

# ANSI 颜色转义序列匹配正则（模块级预编译，避免每次接收数据都重新编译）
_ANSI_PATTERN = re.compile(r'\x1B(?:\[([0-9;]*)m)?')
# 控制字符集合（除 \r \n \t 外的 ASCII < 32 字符需转义显示）
_CONTROL_CHARS_ESCAPE = {chr(i): f'\\x{i:02X}' for i in range(32) if chr(i) not in '\r\n\t'}

# --- 文件操作工作类 ---
class WorkerSignals(QObject):
    """定义工作线程的信号"""
    result = pyqtSignal(tuple)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

class FileOperationWorker(QRunnable):
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.func(self.signals, *self.args, **self.kwargs)
            self.signals.result.emit(result)
        except FileNotFoundError as e:
            self.signals.error.emit(f"文件操作错误: 文件未找到: {e}")
        except PermissionError as e:
            self.signals.error.emit(f"文件操作错误: 权限不足: {e}")
        except IOError as e:
            self.signals.error.emit(f"文件操作错误: I/O错误: {e}")
        except Exception as e:
            self.signals.error.emit(f"文件操作错误: 未知错误: {e}")

# --- 主窗口类 ---
class SerialTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.transport = TransportWrapper()
        self.read_thread = None
        # 网络模式参数
        self.connection_mode = 'serial'
        self.udp_local_ip = '0.0.0.0'
        self.udp_local_port = 8080
        self.udp_remote_ip = '192.168.1.100'
        self.udp_remote_port = 8888
        self.tcp_remote_ip = '192.168.1.100'
        self.tcp_remote_port = 8888
        self.tcp_server_local_ip = '0.0.0.0'
        self.tcp_server_local_port = 8888
        # 日志缓冲区最大大小
        self.MAX_LOG_BUFFER_SIZE = 10000  # 最多存储10000条日志
        # 日志缓冲区，使用deque自动限制大小，pop(0)操作从O(n)降为O(1)
        self.log_buffer = deque(maxlen=self.MAX_LOG_BUFFER_SIZE)
        # 单条日志最大长度限制，防止过长日志占用过多内存
        self.MAX_LOG_ENTRY_LENGTH = 4096
        # 接收区显示的最大行数，超过后自动清理前面的内容，防止界面卡死
        self.MAX_DISPLAY_LINES = 5000  # 最多显示5000行
        # 自动保存相关变量
        self.auto_save_enabled = False
        self.current_log_file = None
        self.log_file_path = ""
        self.log_file_size = 0
        self.max_log_file_size = 200 * 1024 * 1024  # 200MB
        self.log_file_count = 0
        self.save_directory = os.path.join(os.getcwd(), "logs")  # 默认保存目录
        self.max_log_files = 10  # 最大保留的日志文件数量
        
        # 数据统计变量
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.packets = 0
        
        # 线程池用于处理文件操作
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(4)
        
        # 配置文件路径（PID 后缀，避免多实例互相覆盖）
        self._base_config_file = os.path.join(os.getcwd(), "serial_config.json")
        self.config_file = os.path.join(os.getcwd(), f"serial_config_{os.getpid()}.json")
        # 首次启动：从基础配置继承，保证新实例继承上次设置
        if not os.path.exists(self.config_file) and os.path.exists(self._base_config_file):
            try:
                import shutil as _shutil
                _shutil.copy2(self._base_config_file, self.config_file)
            except Exception:
                pass
        
        # 线程安全相关
        self.serial_mutex = QMutex()  # 串口操作互斥锁
        self.error_state = False  # 错误状态标志
        self.stop_file_send = False  # 文件发送取消标志
        self.selected_file_path = None  # 待发送文件路径

        # 发送历史相关
        self.send_history = deque(maxlen=30)  # 发送历史记录，最多30条
        self.history_index = -1               # 当前浏览位置，-1表示未在历史中

        # 筛选相关
        self.filter_enabled = False   # 筛选开关
        self.filter_text = ""         # 筛选关键字
        self.filter_mode = "包含"     # 筛选模式: "包含" / "忽略大小写" / "正则"

        # 主题相关
        self.current_theme = "light"  # 默认亮色模式
        self.theme_colors = dict(THEME_COLORS['light'])

        # 生成下拉箭头图标（QSS 接管 QComboBox 后必须显式提供箭头图片）
        self._arrow_dark_path, self._arrow_light_path = self._make_arrow_icons()


        self.init_ui()
        self.refresh_ports() # 启动时刷新串口列表
        self.load_config() # 启动时加载配置

    def _make_arrow_icons(self):
        """生成下拉箭头 V 型图标（QSS 接管 ComboBox 后系统箭头不显示，必须手绘）"""
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))

        def _draw(path, color_hex, size=12):
            pix = QPixmap(size, size)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(QColor(color_hex))
            p.setPen(Qt.NoPen)
            # ▼ 倒三角
            margin = 3
            cx = size // 2
            tri = QPolygon([
                QPoint(cx, size - margin),
                QPoint(margin, margin + 1),
                QPoint(size - margin, margin + 1),
            ])
            p.drawPolygon(tri)
            p.end()
            pix.save(path, 'PNG')
            return path.replace('\\', '/')

        dark = _draw(os.path.join(script_dir, '_arrow_dark.png'), '#6A7384')
        light = _draw(os.path.join(script_dir, '_arrow_light.png'), '#666666')
        return dark, light

    def init_ui(self):
        self.setWindowTitle("hight-flight串口工具")
        self.resize(1000, 900)
        self.setMinimumSize(720, 560)  # 保证顶部设置栏和发送区控件不重叠

        # 主窗口部件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(4, 0, 4, 4)  # 顶部由菜单栏占据，不留额外边距
        main_layout.setSpacing(4)

        # --- 菜单栏 ---
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        act_save_log = QAction("保存接收日志(&S)", self)
        act_save_log.setShortcut("Ctrl+S")
        act_save_log.triggered.connect(self.save_log_manually)
        file_menu.addAction(act_save_log)
        act_export = QAction("导出接收数据(&E)...", self)
        act_export.triggered.connect(self.export_data)
        file_menu.addAction(act_export)
        file_menu.addSeparator()
        act_exit = QAction("退出(&X)", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # 视图菜单
        view_menu = menubar.addMenu("视图(&V)")
        self.act_dark_mode = QAction("暗黑模式(&D)", self)
        self.act_dark_mode.setCheckable(True)
        self.act_dark_mode.setChecked(self.current_theme == 'dark')
        self.act_dark_mode.triggered.connect(self.toggle_theme)
        view_menu.addAction(self.act_dark_mode)
        self.act_multi_send = QAction("多字符发送(&M)", self)
        self.act_multi_send.setCheckable(True)
        self.act_multi_send.triggered.connect(self.toggle_multi_send)
        view_menu.addAction(self.act_multi_send)
        self.act_timestamp = QAction("显示时间戳(&T)", self)
        self.act_timestamp.setCheckable(True)
        self.act_timestamp.setChecked(True)
        self.act_timestamp.triggered.connect(self._toggle_timestamp)
        view_menu.addAction(self.act_timestamp)
        view_menu.addSeparator()
        act_clear_recv_menu = QAction("清空接收区(&R)", self)
        act_clear_recv_menu.triggered.connect(self.clear_recv_area)
        view_menu.addAction(act_clear_recv_menu)
        act_clear_send_menu = QAction("清空发送区(&E)", self)
        act_clear_send_menu.triggered.connect(self.clear_send_area)
        view_menu.addAction(act_clear_send_menu)

        # 工具菜单
        tool_menu = menubar.addMenu("工具(&T)")
        act_crc = QAction("CRC 计算器", self)
        act_crc.triggered.connect(self.crc_calculator)
        tool_menu.addAction(act_crc)
        act_hex = QAction("HEX 转换器", self)
        act_hex.triggered.connect(self.hex_converter)
        tool_menu.addAction(act_hex)
        act_monitor = QAction("串口监视器", self)
        act_monitor.triggered.connect(self.serial_monitor)
        tool_menu.addAction(act_monitor)
        act_scope = QAction("数据波形（示波器）", self)
        act_scope.triggered.connect(self.oscilloscope)
        tool_menu.addAction(act_scope)
        act_modbus = QAction("Modbus 工具", self)
        act_modbus.triggered.connect(self.modbus_tool)
        tool_menu.addAction(act_modbus)
        act_json = QAction("数据分析面板(&J)", self)
        act_json.triggered.connect(self.data_viewer)
        tool_menu.addAction(act_json)
        tool_menu.addSeparator()
        act_ota = QAction("OTA 升级控制中心(&O)", self)
        act_ota.triggered.connect(self.open_ota_center)
        tool_menu.addAction(act_ota)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        act_usage = QAction("使用说明(&U)", self)
        act_usage.triggered.connect(self.show_usage)
        help_menu.addAction(act_usage)
        act_about = QAction("关于(&A)", self)
        act_about.triggered.connect(self.show_about)
        help_menu.addAction(act_about)

        # --- 顶部设置区域 ---
        serial_group = QGroupBox("通信设置")
        serial_group.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        serial_layout = QVBoxLayout(serial_group)
        serial_layout.setContentsMargins(2, 2, 2, 2)
        serial_layout.setSpacing(1)

        # 第一行：模式选择 + 刷新 + 操作按钮（非串口模式显示）
        self.mode_layout = QHBoxLayout()
        self.mode_layout.setSpacing(4)

        mode_label = QLabel("模式:")
        mode_label.setFont(QFont("Microsoft YaHei", 9))
        self.mode_layout.addWidget(mode_label)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(['串口 (Serial)', 'UDP', 'TCP Client', 'TCP Server'])
        self.combo_mode.setFont(QFont("Microsoft YaHei", 9))
        self.combo_mode.setMinimumWidth(120)
        self.combo_mode.currentIndexChanged.connect(self.on_mode_changed)
        self.mode_layout.addWidget(self.combo_mode)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setMinimumWidth(56)
        self.btn_refresh.setFont(QFont("Microsoft YaHei", 9))
        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.mode_layout.addWidget(self.btn_refresh)

        # 操作按钮（仅非串口模式显示）
        self.btn_switch = QPushButton("打开连接")
        self.btn_switch.setCheckable(True)
        self.btn_switch.setFont(QFont("Microsoft YaHei", 9))
        self.btn_switch.setMinimumWidth(80)
        self.btn_switch.clicked.connect(self.toggle_connection)
        self.mode_layout.addWidget(self.btn_switch)

        self.btn_more_settings = QPushButton("更多设置")
        self.btn_more_settings.setFont(QFont("Microsoft YaHei", 9))
        self.btn_more_settings.setMinimumWidth(90)
        self.btn_more_settings.clicked.connect(self.show_more_settings)
        self.mode_layout.addWidget(self.btn_more_settings)

        self.mode_layout.addStretch()
        serial_layout.addLayout(self.mode_layout)

        # 第二行：动态参数面板 + 操作按钮（仅串口模式在参数后显示按钮）
        self.params_row = QHBoxLayout()
        self.params_row.setSpacing(4)

        self.stack_params = QStackedWidget()
        self.stack_params.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Page 0: 串口参数
        serial_page = QWidget()
        serial_page_layout = QHBoxLayout(serial_page)
        serial_page_layout.setContentsMargins(0, 0, 0, 0)
        serial_page_layout.setSpacing(4)

        port_layout = QHBoxLayout()
        port_label = QLabel("串口:")
        port_label.setFont(QFont("Microsoft YaHei", 9))
        port_layout.addWidget(port_label)
        self.combo_port = QComboBox()
        self.combo_port.setMinimumWidth(100)
        self.combo_port.setFont(QFont("Consolas", 9))
        port_layout.addWidget(self.combo_port)
        serial_page_layout.addLayout(port_layout)

        # 串口模式刷新按钮（串口选择后面）
        self.btn_refresh_serial = QPushButton("刷新")
        self.btn_refresh_serial.setMinimumWidth(56)
        self.btn_refresh_serial.setFont(QFont("Microsoft YaHei", 9))
        self.btn_refresh_serial.clicked.connect(self.refresh_ports)
        serial_page_layout.addWidget(self.btn_refresh_serial)

        baud_layout = QHBoxLayout()
        baud_label = QLabel("波特率:")
        baud_label.setFont(QFont("Microsoft YaHei", 9))
        baud_layout.addWidget(baud_label)
        self.combo_baud = QComboBox()
        self.combo_baud.addItems(['9600', '19200', '38400', '57600', '115200', '自定义'])
        self.combo_baud.setCurrentText('115200')
        self.combo_baud.setFont(QFont("Consolas", 9))
        self.combo_baud.currentIndexChanged.connect(self.handle_baud_change)
        baud_layout.addWidget(self.combo_baud)
        serial_page_layout.addLayout(baud_layout)

        # 串口模式按钮（紧接波特率后面）
        self.btn_switch_serial = QPushButton("打开连接")
        self.btn_switch_serial.setCheckable(True)
        self.btn_switch_serial.setFont(QFont("Microsoft YaHei", 9))
        self.btn_switch_serial.setMinimumWidth(80)
        self.btn_switch_serial.clicked.connect(self.toggle_connection)
        serial_page_layout.addWidget(self.btn_switch_serial)

        self.btn_more_settings_serial = QPushButton("更多设置")
        self.btn_more_settings_serial.setFont(QFont("Microsoft YaHei", 9))
        self.btn_more_settings_serial.setMinimumWidth(90)
        self.btn_more_settings_serial.clicked.connect(self.show_more_settings)
        serial_page_layout.addWidget(self.btn_more_settings_serial)

        serial_page_layout.addStretch()
        self.stack_params.addWidget(serial_page)  # index 0

        # Page 1: UDP 参数
        udp_page = QWidget()
        udp_page_layout = QHBoxLayout(udp_page)
        udp_page_layout.setContentsMargins(0, 0, 0, 0)
        udp_page_layout.setSpacing(4)
        udp_page_layout.addWidget(QLabel("本地IP:", font=QFont("Microsoft YaHei", 9)))
        self.edit_udp_local_ip = QComboBox()
        self.edit_udp_local_ip.setEditable(True)
        self.edit_udp_local_ip.setFont(QFont("Consolas", 9))
        self.edit_udp_local_ip.setMinimumWidth(150)
        self.edit_udp_local_ip.setFixedHeight(26)
        self.edit_udp_local_ip.addItem('0.0.0.0')
        udp_page_layout.addWidget(self.edit_udp_local_ip)
        udp_page_layout.addWidget(QLabel("本地端口:", font=QFont("Microsoft YaHei", 9)))
        self.edit_udp_local_port = QSpinBox()
        self.edit_udp_local_port.setFont(QFont("Consolas", 9))
        self.edit_udp_local_port.setRange(1, 65535)
        self.edit_udp_local_port.setValue(8080)
        self.edit_udp_local_port.setMaximumWidth(70)
        self.edit_udp_local_port.setFixedHeight(26)
        udp_page_layout.addWidget(self.edit_udp_local_port)
        udp_page_layout.addWidget(QLabel("远程IP:", font=QFont("Microsoft YaHei", 9)))
        self.edit_udp_remote_ip = QLineEdit()
        self.edit_udp_remote_ip.setFont(QFont("Consolas", 9))
        self.edit_udp_remote_ip.setMinimumWidth(120)
        self.edit_udp_remote_ip.setFixedHeight(26)
        self.edit_udp_remote_ip.setText('192.168.1.100')
        udp_page_layout.addWidget(self.edit_udp_remote_ip)
        udp_page_layout.addWidget(QLabel("远程端口:", font=QFont("Microsoft YaHei", 9)))
        self.edit_udp_remote_port = QSpinBox()
        self.edit_udp_remote_port.setFont(QFont("Consolas", 9))
        self.edit_udp_remote_port.setRange(1, 65535)
        self.edit_udp_remote_port.setValue(8888)
        self.edit_udp_remote_port.setMaximumWidth(70)
        self.edit_udp_remote_port.setFixedHeight(26)
        udp_page_layout.addWidget(self.edit_udp_remote_port)
        udp_page_layout.addStretch()
        self.stack_params.addWidget(udp_page)  # index 1

        # Page 2: TCP Client 参数
        tcp_client_page = QWidget()
        tcp_client_page_layout = QHBoxLayout(tcp_client_page)
        tcp_client_page_layout.setContentsMargins(0, 0, 0, 0)
        tcp_client_page_layout.setSpacing(4)
        tcp_client_page_layout.addWidget(QLabel("远程IP:", font=QFont("Microsoft YaHei", 9)))
        self.edit_tcp_remote_ip = QLineEdit()
        self.edit_tcp_remote_ip.setFont(QFont("Consolas", 9))
        self.edit_tcp_remote_ip.setMinimumWidth(120)
        self.edit_tcp_remote_ip.setFixedHeight(26)
        self.edit_tcp_remote_ip.setText('192.168.1.100')
        tcp_client_page_layout.addWidget(self.edit_tcp_remote_ip)
        tcp_client_page_layout.addWidget(QLabel("远程端口:", font=QFont("Microsoft YaHei", 9)))
        self.edit_tcp_remote_port = QSpinBox()
        self.edit_tcp_remote_port.setFont(QFont("Consolas", 9))
        self.edit_tcp_remote_port.setRange(1, 65535)
        self.edit_tcp_remote_port.setValue(8888)
        self.edit_tcp_remote_port.setMaximumWidth(70)
        self.edit_tcp_remote_port.setFixedHeight(26)
        tcp_client_page_layout.addWidget(self.edit_tcp_remote_port)
        tcp_client_page_layout.addStretch()
        self.stack_params.addWidget(tcp_client_page)  # index 2

        # Page 3: TCP Server 参数
        tcp_server_page = QWidget()
        tcp_server_page_layout = QHBoxLayout(tcp_server_page)
        tcp_server_page_layout.setContentsMargins(0, 0, 0, 0)
        tcp_server_page_layout.setSpacing(4)
        tcp_server_page_layout.addWidget(QLabel("本地IP:", font=QFont("Microsoft YaHei", 9)))
        self.edit_tcp_server_local_ip = QComboBox()
        self.edit_tcp_server_local_ip.setEditable(True)
        self.edit_tcp_server_local_ip.setFont(QFont("Consolas", 9))
        self.edit_tcp_server_local_ip.setMinimumWidth(150)
        self.edit_tcp_server_local_ip.setFixedHeight(26)
        self.edit_tcp_server_local_ip.addItem('0.0.0.0')
        tcp_server_page_layout.addWidget(self.edit_tcp_server_local_ip)
        tcp_server_page_layout.addWidget(QLabel("本地端口:", font=QFont("Microsoft YaHei", 9)))
        self.edit_tcp_server_local_port = QSpinBox()
        self.edit_tcp_server_local_port.setFont(QFont("Consolas", 9))
        self.edit_tcp_server_local_port.setRange(1, 65535)
        self.edit_tcp_server_local_port.setValue(8888)
        self.edit_tcp_server_local_port.setMaximumWidth(70)
        self.edit_tcp_server_local_port.setFixedHeight(26)
        tcp_server_page_layout.addWidget(self.edit_tcp_server_local_port)
        tcp_server_page_layout.addStretch()
        self.stack_params.addWidget(tcp_server_page)  # index 3

        self.params_row.addWidget(self.stack_params)

        # 自动保存（合并在参数行右侧，所有模式统一）
        self.params_row.addSpacing(16)
        self.check_auto_save = QCheckBox("自动保存日志")
        self.check_auto_save.setFont(QFont("Microsoft YaHei", 9))
        self.check_auto_save.stateChanged.connect(self.toggle_auto_save)
        self.params_row.addWidget(self.check_auto_save)

        self.label_save_path = QLabel("路径:")
        self.label_save_path.setFont(QFont("Microsoft YaHei", 9))
        self.params_row.addWidget(self.label_save_path)
        self.line_edit_save_path = QLineEdit()
        self.line_edit_save_path.setReadOnly(True)
        self.line_edit_save_path.setText(self.save_directory)
        self.line_edit_save_path.setFont(QFont("Consolas", 9))
        self.line_edit_save_path.setMaximumWidth(240)
        self.line_edit_save_path.setFixedHeight(26)
        self.params_row.addWidget(self.line_edit_save_path)
        self.btn_browse_path = QPushButton("浏览")
        self.btn_browse_path.setMinimumWidth(56)
        self.btn_browse_path.setFont(QFont("Microsoft YaHei", 9))
        self.btn_browse_path.clicked.connect(self.browse_save_path)
        self.params_row.addWidget(self.btn_browse_path)

        serial_layout.addLayout(self.params_row)

        # 初始状态：串口模式，隐藏 mode_layout 按钮
        self.btn_switch.setVisible(False)
        self.btn_more_settings.setVisible(False)
        self.btn_refresh.setVisible(False)

        # 第三行：显示、筛选与编码设置
        display_save_layout = QHBoxLayout()
        display_save_layout.setSpacing(4)

        self.check_hex_recv = QCheckBox("HEX显示")
        self.check_hex_recv.setFont(QFont("Microsoft YaHei", 9))
        display_save_layout.addWidget(self.check_hex_recv)

        self.check_filter = QCheckBox("筛选:")
        self.check_filter.setFont(QFont("Microsoft YaHei", 9))
        self.check_filter.stateChanged.connect(self.toggle_filter)
        display_save_layout.addWidget(self.check_filter)

        self.edit_filter = QLineEdit()
        self.edit_filter.setFont(QFont("Consolas", 9))
        self.edit_filter.setPlaceholderText("输入关键字，逗号分隔...")
        self.edit_filter.setMaximumWidth(180)
        self.edit_filter.setFixedHeight(26)
        self.edit_filter.setEnabled(False)
        self.edit_filter.textChanged.connect(self.update_filter_text)
        display_save_layout.addWidget(self.edit_filter)

        self.combo_filter_mode = QComboBox()
        self.combo_filter_mode.addItems(["包含", "忽略大小写", "正则", "高亮显示"])
        self.combo_filter_mode.setFont(QFont("Microsoft YaHei", 9))
        self.combo_filter_mode.setMaximumWidth(90)
        self.combo_filter_mode.setEnabled(False)
        self.combo_filter_mode.currentTextChanged.connect(self.update_filter_mode)
        display_save_layout.addWidget(self.combo_filter_mode)

        encoding_label = QLabel("编码:")
        encoding_label.setFont(QFont("Microsoft YaHei", 9))
        display_save_layout.addWidget(encoding_label)
        self.combo_encoding = QComboBox()
        self.combo_encoding.addItems(['UTF-8', 'GBK', 'GB2312', 'ASCII', 'ISO-8859-1', 'GB18030'])
        self.combo_encoding.setCurrentText('UTF-8')
        self.combo_encoding.setFont(QFont("Microsoft YaHei", 9))
        self.combo_encoding.setMinimumWidth(80)
        display_save_layout.addWidget(self.combo_encoding)

        self.check_timestamp = QCheckBox("显示时间")
        self.check_timestamp.setChecked(True)
        self.check_timestamp.setFont(QFont("Microsoft YaHei", 9))
        self.check_timestamp.stateChanged.connect(
            lambda checked: self.act_timestamp.setChecked(checked)
        )
        display_save_layout.addWidget(self.check_timestamp)

        self.btn_clear_recv = QPushButton("清空接收")
        self.btn_clear_recv.setFont(QFont("Microsoft YaHei", 9))
        self.btn_clear_recv.setMinimumWidth(80)
        self.btn_clear_recv.clicked.connect(self.clear_recv_area)
        display_save_layout.addWidget(self.btn_clear_recv)
        display_save_layout.addStretch()

        serial_layout.addLayout(display_save_layout)

        main_layout.addWidget(serial_group)

        # --- 中间接收和发送区域（使用分割器）---
        splitter = QSplitter(Qt.Vertical)
        
        # 接收区
        recv_group = QGroupBox("接收区")
        recv_group.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        recv_layout = QVBoxLayout(recv_group)
        recv_layout.setContentsMargins(4, 4, 4, 4)

        self.text_recv = QTextEdit()
        self.text_recv.setReadOnly(True)
        self.text_recv.setFont(QFont("Consolas", 11, QFont.Normal))
        self.text_recv.setPlaceholderText("等待接收数据…")
        recv_layout.addWidget(self.text_recv)

        # 接收区滚轮跟底控制：用户滚动查看时暂停自动跟底，停止滚动 20 秒后恢复
        self._recv_user_reading = False  # 用户正在滚动查看（暂停跟底）
        self._recv_follow_timer = QTimer(self)
        self._recv_follow_timer.setSingleShot(True)
        self._recv_follow_timer.setInterval(20000)  # 滚轮静止 20 秒后恢复跟底
        self._recv_follow_timer.timeout.connect(self._on_recv_follow_resume)
        self.text_recv.viewport().installEventFilter(self)

        # 接收区显示刷新兜底定时器：数据停止后把节流缓冲区最后一笔也刷出来
        self._last_display_update_time = 0.0
        self._pending_display_data = []
        self._recv_flush_timer = QTimer(self)
        self._recv_flush_timer.setSingleShot(True)
        self._recv_flush_timer.setInterval(50)  # 50ms 兜底刷新
        self._recv_flush_timer.timeout.connect(self._flush_pending_display)

        # 日志定时刷盘定时器：避免每条数据都 fsync 阻塞 UI 线程
        self._log_fsync_timer = QTimer(self)
        self._log_fsync_timer.setSingleShot(False)
        self._log_fsync_timer.setInterval(2000)  # 每 2 秒强制落盘一次
        self._log_fsync_timer.timeout.connect(self._fsync_log_file)

        # 数据统计行（扁平布局，无多余边框）
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(12)

        # 接收字节数统计
        self.label_rx_bytes = QLabel("接收字节: 0")
        self.label_rx_bytes.setFont(QFont("Consolas", 9))
        stats_layout.addWidget(self.label_rx_bytes)

        # 发送字节数统计
        self.label_tx_bytes = QLabel("发送字节: 0")
        self.label_tx_bytes.setFont(QFont("Consolas", 9))
        stats_layout.addWidget(self.label_tx_bytes)

        # 数据包数量统计
        self.label_packets = QLabel("数据包: 0")
        self.label_packets.setFont(QFont("Consolas", 9))
        stats_layout.addWidget(self.label_packets)

        stats_layout.addStretch()

        recv_layout.addLayout(stats_layout)
        splitter.addWidget(recv_group)
        
        # 发送区
        send_group = QGroupBox("发送区")
        send_group.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        send_layout = QVBoxLayout(send_group)
        send_layout.setContentsMargins(4, 4, 4, 4)
        send_layout.setSpacing(4)
        
        # 发送设置行
        send_settings_layout = QHBoxLayout()
        send_settings_layout.setSpacing(4)

        # 发送区设置：HEX发送
        self.check_hex_send = QCheckBox("HEX发送")
        self.check_hex_send.setFont(QFont("Microsoft YaHei", 9))
        self.check_hex_send.stateChanged.connect(self._validate_hex_input)
        send_settings_layout.addWidget(self.check_hex_send)

        # 回车换行勾选选项
        self.check_newline = QCheckBox("回车换行")
        self.check_newline.setFont(QFont("Microsoft YaHei", 9))
        send_settings_layout.addWidget(self.check_newline)

        # RTS和DTR控制选项
        self.check_rts = QCheckBox("RTS")
        self.check_rts.setFont(QFont("Microsoft YaHei", 9))
        self.check_rts.stateChanged.connect(self.update_rts_dtr)
        send_settings_layout.addWidget(self.check_rts)

        self.check_dtr = QCheckBox("DTR")
        self.check_dtr.setFont(QFont("Microsoft YaHei", 9))
        self.check_dtr.stateChanged.connect(self.update_rts_dtr)
        send_settings_layout.addWidget(self.check_dtr)

        # 校验选项（使用子布局，设置更小的间距）
        checksum_layout = QHBoxLayout()
        checksum_layout.setSpacing(4)

        self.label_checksum = QLabel("校验:")
        self.label_checksum.setFont(QFont("Microsoft YaHei", 9))
        checksum_layout.addWidget(self.label_checksum)

        self.combo_checksum = QComboBox()
        self.combo_checksum.addItems(["None", "ModbusCRC16", "CRC32", "Fletcher", "XOR8", "ADD8", "ADD16"])
        self.combo_checksum.setFont(QFont("Consolas", 9))
        checksum_layout.addWidget(self.combo_checksum)

        send_settings_layout.addLayout(checksum_layout)

        # 重复发送相关控件
        repeat_layout = QHBoxLayout()
        repeat_layout.setSpacing(4)

        self.check_repeat = QCheckBox("重复发送")
        self.check_repeat.setFont(QFont("Microsoft YaHei", 9))
        repeat_layout.addWidget(self.check_repeat)

        repeat_layout.addWidget(QLabel("间隔(ms):"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(100, 5000)
        self.spin_interval.setValue(1000)
        self.spin_interval.setFont(QFont("Consolas", 9))
        self.spin_interval.setEnabled(False)  # 默认禁用
        repeat_layout.addWidget(self.spin_interval)

        send_settings_layout.addLayout(repeat_layout)
        send_settings_layout.addStretch()

        send_layout.addLayout(send_settings_layout)
        
        # 首尾字段输入框（平行布局，紧凑版）
        fields_layout = QHBoxLayout()
        fields_layout.setSpacing(4)

        # 发送首字段
        head_field_layout = QHBoxLayout()
        head_field_layout.setSpacing(4)
        self.check_head_field = QCheckBox()
        head_field_layout.addWidget(self.check_head_field)
        head_label = QLabel("首字段:")
        head_label.setFont(QFont("Microsoft YaHei", 9))
        head_field_layout.addWidget(head_label)
        self.text_ota = QLineEdit()
        self.text_ota.setFont(QFont("Consolas", 9))
        self.text_ota.setPlaceholderText("输入首字段...")
        self.text_ota.setFixedHeight(26)
        head_field_layout.addWidget(self.text_ota)
        fields_layout.addLayout(head_field_layout)

        # 发送尾字段
        tail_field_layout = QHBoxLayout()
        tail_field_layout.setSpacing(4)
        self.check_tail_field = QCheckBox()
        tail_field_layout.addWidget(self.check_tail_field)
        tail_label = QLabel("尾字段:")
        tail_label.setFont(QFont("Microsoft YaHei", 9))
        tail_field_layout.addWidget(tail_label)
        self.text_tail = QLineEdit()
        self.text_tail.setFont(QFont("Consolas", 9))
        self.text_tail.setPlaceholderText("输入尾字段...")
        self.text_tail.setFixedHeight(26)
        tail_field_layout.addWidget(self.text_tail)
        fields_layout.addLayout(tail_field_layout)

        # 文件发送（内联到头尾字段行）
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setFont(QFont("Consolas", 9))
        self.file_path_edit.setPlaceholderText("选择要发送的文件...")
        self.file_path_edit.setReadOnly(True)
        self.file_path_edit.setMaximumWidth(180)
        self.file_path_edit.setFixedHeight(26)
        fields_layout.addWidget(self.file_path_edit)

        self.btn_select_file = QPushButton("选择文件")
        self.btn_select_file.setFont(QFont("Microsoft YaHei", 9))
        self.btn_select_file.setMinimumWidth(72)
        self.btn_select_file.clicked.connect(self.select_file_to_send)
        fields_layout.addWidget(self.btn_select_file)

        self.btn_send_file = QPushButton("发送文件")
        self.btn_send_file.setFont(QFont("Microsoft YaHei", 9))
        self.btn_send_file.setMinimumWidth(72)
        self.btn_send_file.clicked.connect(self.send_file)
        self.btn_send_file.setEnabled(False)  # 默认禁用，选择文件后启用
        fields_layout.addWidget(self.btn_send_file)

        fields_layout.addStretch()

        send_layout.addLayout(fields_layout)
        
        # 发送输入框
        self.text_send = QTextEdit()
        self.text_send.setMinimumHeight(40)
        self.text_send.setMaximumHeight(100)  # 放宽上限，允许显示约 4 行
        self.text_send.setFont(QFont("Consolas", 11, QFont.Normal))
        self.text_send.setPlaceholderText("在此输入要发送的内容...")
        self.text_send.textChanged.connect(self._validate_hex_input)
        self.text_send.textChanged.connect(self._on_send_text_changed)
        self.text_send.installEventFilter(self)  # 捕获上下键实现历史记录导航
        self._setting_history_text = False       # 标志：是否正在由历史导航设置文本
        # 首/尾字段 HEX 校验同步
        self.text_ota.textChanged.connect(self._validate_hex_input)
        self.text_tail.textChanged.connect(self._validate_hex_input)
        send_layout.addWidget(self.text_send)

        # 发送按钮行
        send_buttons_layout = QHBoxLayout()
        send_buttons_layout.setSpacing(4)

        self.btn_send = QPushButton("发送")
        self.btn_send.setFont(QFont("Microsoft YaHei", 9))
        self.btn_send.setMinimumWidth(80)
        self.btn_send.clicked.connect(self.send_data)
        self.btn_send.setShortcut("Ctrl+Return")

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setFont(QFont("Microsoft YaHei", 9))
        self.btn_stop.setMinimumWidth(56)
        self.btn_stop.clicked.connect(self.stop_repeat)
        self.btn_stop.setEnabled(False)

        self.btn_clear_send = QPushButton("清空发送")
        self.btn_clear_send.setFont(QFont("Microsoft YaHei", 9))
        self.btn_clear_send.setMinimumWidth(72)
        self.btn_clear_send.clicked.connect(self.clear_send_area)

        send_buttons_layout.addWidget(self.btn_send)
        send_buttons_layout.addWidget(self.btn_stop)
        send_buttons_layout.addWidget(self.btn_clear_send)
        send_buttons_layout.addStretch()

        self.btn_save_params = QPushButton("保存参数")
        self.btn_save_params.setFont(QFont("Microsoft YaHei", 9))
        self.btn_save_params.setMinimumWidth(72)
        self.btn_save_params.clicked.connect(self.save_config)
        send_buttons_layout.addWidget(self.btn_save_params)

        self.btn_toggle_multi_send = QPushButton("显示多字符发送")
        self.btn_toggle_multi_send.setFont(QFont("Microsoft YaHei", 9))
        self.btn_toggle_multi_send.setMinimumWidth(110)
        self.btn_toggle_multi_send.clicked.connect(self.toggle_multi_send)
        send_buttons_layout.addWidget(self.btn_toggle_multi_send)

        send_layout.addLayout(send_buttons_layout)
        splitter.addWidget(send_group)
        
        # 设置分割器的初始大小比例（接收区约 78%，发送区约 22%）
        splitter.setSizes([700, 200])
        
        # 添加多字符发送区域（默认隐藏）
        self.multi_send_widget = QWidget()
        self.multi_send_layout = QVBoxLayout(self.multi_send_widget)
        self.multi_send_layout.setContentsMargins(0, 0, 0, 0)
        self.multi_send_layout.setSpacing(8)
        
        # 多字符发送组
        multi_send_group = QGroupBox("多字符串发送")
        multi_send_group.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        multi_send_group_layout = QVBoxLayout(multi_send_group)
        multi_send_group_layout.setContentsMargins(8, 8, 8, 8)
        multi_send_group_layout.setSpacing(8)
        
        # 工具栏
        toolbar_layout = QHBoxLayout()
        
        # 循环发送复选框（用于控制批量发送的开始/停止）
        self.check_cycle_send = QCheckBox("循环发送")
        self.check_cycle_send.setFont(QFont("Microsoft YaHei", 9))
        self.check_cycle_send.stateChanged.connect(self.toggle_batch_send)
        toolbar_layout.addWidget(self.check_cycle_send)
        
        # 延时标签和输入框
        delay_label = QLabel("延时:")
        delay_label.setFont(QFont("Microsoft YaHei", 9))
        toolbar_layout.addWidget(delay_label)
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 10000)
        self.spin_delay.setValue(1000)
        self.spin_delay.setFont(QFont("Consolas", 9))
        self.spin_delay.setMinimumWidth(60)
        toolbar_layout.addWidget(self.spin_delay)
        ms_label = QLabel("ms")
        ms_label.setFont(QFont("Microsoft YaHei", 9))
        toolbar_layout.addWidget(ms_label)
        
        # 循环次数勾选按钮和输入框
        self.check_cycle_count = QCheckBox("次数:")
        self.check_cycle_count.setFont(QFont("Microsoft YaHei", 9))
        toolbar_layout.addWidget(self.check_cycle_count)
        
        self.spin_cycle_count = QSpinBox()
        self.spin_cycle_count.setRange(1, 9999)
        self.spin_cycle_count.setValue(1)
        self.spin_cycle_count.setFont(QFont("Consolas", 9))
        self.spin_cycle_count.setMinimumWidth(60)
        self.spin_cycle_count.setEnabled(False)  # 默认禁用
        toolbar_layout.addWidget(self.spin_cycle_count)
        
        # 连接信号
        self.check_cycle_count.stateChanged.connect(self.toggle_cycle_count)
        
        toolbar_layout.addStretch()
        multi_send_group_layout.addLayout(toolbar_layout)
        
        # 创建第二行布局，用于放置保存、加载和帮助按钮
        button_row_layout = QHBoxLayout()
        
        # 保存/加载按钮
        btn_save = QPushButton("保存")
        btn_save.setFont(QFont("Microsoft YaHei", 9))
        btn_save.setMinimumWidth(56)
        btn_save.clicked.connect(self.save_multi_items)
        button_row_layout.addWidget(btn_save)
        
        btn_load = QPushButton("加载")
        btn_load.setFont(QFont("Microsoft YaHei", 9))
        btn_load.setMinimumWidth(56)
        btn_load.clicked.connect(self.load_multi_items)
        button_row_layout.addWidget(btn_load)
        
        # 帮助按钮
        btn_help = QPushButton("帮助")
        btn_help.setFont(QFont("Microsoft YaHei", 9))
        btn_help.setMinimumWidth(56)
        btn_help.clicked.connect(self.show_multi_send_help)
        button_row_layout.addWidget(btn_help)
        
        button_row_layout.addStretch()
        multi_send_group_layout.addLayout(button_row_layout)
        
        # 多字符列表
        self.table_multi_send = QTableWidget()
        self.table_multi_send.setColumnCount(5)
        self.table_multi_send.setHorizontalHeaderLabels(["HEX", "字符串", "点击发送", "延时(ms)", "顺序"])
        
        # 设置编辑触发模式为单击
        self.table_multi_send.setEditTriggers(QAbstractItemView.CurrentChanged | QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        
        # 设置列宽调整模式
        header = self.table_multi_send.horizontalHeader()
        # 设置列宽调整模式为可交互，允许手动调整
        header.setSectionResizeMode(0, QHeaderView.Interactive)  # HEX 列
        header.setSectionResizeMode(1, QHeaderView.Interactive)  # 字符串列
        header.setSectionResizeMode(2, QHeaderView.Interactive)  # 点击发送列
        header.setSectionResizeMode(3, QHeaderView.Interactive)  # 延时列
        header.setSectionResizeMode(4, QHeaderView.Interactive)  # 顺序列
        
        # 设置初始列宽
        self.table_multi_send.setColumnWidth(0, 40)   # HEX 列
        self.table_multi_send.setColumnWidth(1, 80)  # 字符串列
        self.table_multi_send.setColumnWidth(2, 80)  # 点击发送列
        self.table_multi_send.setColumnWidth(3, 70)   # 延时列
        self.table_multi_send.setColumnWidth(4, 50)   # 顺序列
        # 字符串列自动填充剩余空间，避免窗口拉宽后右侧空白
        header.setSectionResizeMode(1, QHeaderView.Stretch)

        # 确保表格充满可用空间
        self.table_multi_send.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        

        
        # 添加默认条目（只有一行，字符串为空，按钮为无注释）
        default_items = [
            [False, "", "无注释", 1000, "1"],  # 默认顺序为1
        ]
        
        self.table_multi_send.setRowCount(len(default_items))
        for i, item in enumerate(default_items):
            # HEX复选框
            hex_checkbox = QCheckBox()
            hex_checkbox.setChecked(item[0])
            hex_widget = QWidget()
            hex_layout = QHBoxLayout(hex_widget)
            hex_layout.addWidget(hex_checkbox)
            hex_layout.setAlignment(Qt.AlignCenter)
            hex_layout.setContentsMargins(0, 0, 0, 0)
            self.table_multi_send.setCellWidget(i, 0, hex_widget)
            
            # 字符串（可双击编辑）
            string_item = QTableWidgetItem(item[1])
            string_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table_multi_send.setItem(i, 1, string_item)
            
            # 发送按钮（右键重命名）
            send_btn = QPushButton(item[2])
            send_btn.setFont(QFont("Microsoft YaHei", 9))
            send_btn.setMinimumWidth(70)  # 增加按钮宽度
            send_btn.clicked.connect(self.on_send_multi_btn_clicked)
            send_btn.setObjectName(f"btn_{i}")
            send_btn.installEventFilter(self)
            send_widget = QWidget()
            send_layout = QHBoxLayout(send_widget)
            send_layout.addWidget(send_btn)
            send_layout.setAlignment(Qt.AlignCenter)
            send_layout.setContentsMargins(0, 0, 0, 0)
            self.table_multi_send.setCellWidget(i, 2, send_widget)
            
            # 延时
            delay_spin = QSpinBox()
            delay_spin.setRange(0, 10000)
            delay_spin.setValue(item[3])
            delay_spin.setFont(QFont("Consolas", 9))
            delay_widget = QWidget()
            delay_layout = QHBoxLayout(delay_widget)
            delay_layout.addWidget(delay_spin)
            delay_layout.setAlignment(Qt.AlignCenter)
            delay_layout.setContentsMargins(0, 0, 0, 0)
            self.table_multi_send.setCellWidget(i, 3, delay_widget)
            
            # 顺序显示框
            order_item = QTableWidgetItem(item[4])
            order_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            order_item.setTextAlignment(Qt.AlignCenter)  # 文本居中显示
            self.table_multi_send.setItem(i, 4, order_item)
        
        # 设置表格属性
        self.table_multi_send.verticalHeader().setVisible(False)
        self.table_multi_send.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_multi_send.setSelectionBehavior(QAbstractItemView.SelectRows)
        # 连接双击事件
        
        multi_send_group_layout.addWidget(self.table_multi_send)
        
        # 添加/删除按钮
        btn_layout = QHBoxLayout()
        btn_add = QPushButton("＋ 添加")
        btn_add.setFont(QFont("Microsoft YaHei", 9))
        btn_add.setMinimumWidth(70)
        btn_add.clicked.connect(self.add_multi_item)
        btn_layout.addWidget(btn_add)

        btn_remove = QPushButton("− 删除")
        btn_remove.setFont(QFont("Microsoft YaHei", 9))
        btn_remove.setMinimumWidth(70)
        btn_remove.clicked.connect(self.remove_multi_item)
        btn_layout.addWidget(btn_remove)
        
        # 清空指令按钮
        btn_clear = QPushButton("清空指令")
        btn_clear.setFont(QFont("Microsoft YaHei", 9))
        btn_clear.setMinimumWidth(40)
        btn_clear.clicked.connect(self.clear_all_items)
        btn_layout.addWidget(btn_clear)
        
        btn_layout.addStretch()
        multi_send_group_layout.addLayout(btn_layout)
        
        self.multi_send_layout.addWidget(multi_send_group)
        
        # 创建一个水平分割器，用于在右侧放置多字符发送区域
        self.main_splitter = QSplitter(Qt.Horizontal)
        # 设置分割器手柄宽度
        self.main_splitter.setHandleWidth(8)
        # 启用不透明调整
        self.main_splitter.setOpaqueResize(True)
        # 设置分割器的伸缩因子
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 1)
        
        # 创建左侧内容
        left_content = QWidget()
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(serial_group)
        left_layout.addWidget(splitter)
        # 设置左侧内容的大小策略
        left_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 创建右侧内容（多字符发送区域）
        right_content = QWidget()
        right_layout = QVBoxLayout(right_content)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        right_layout.addWidget(self.multi_send_widget)
        # 设置右侧内容的大小策略
        right_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 添加到分割器
        self.main_splitter.addWidget(left_content)
        self.main_splitter.addWidget(right_content)
        
        # 设置分割器大小，左侧占90%，右侧占10%
        self.main_splitter.setSizes([900, 100])
        
        # 初始隐藏多字符发送区域
        right_content.hide()
        # 调整左侧大小
        self.main_splitter.setSizes([1000, 0])
        
        # 替换原来的布局
        main_layout.addWidget(self.main_splitter)
        
        # 重复发送定时器
        self.repeat_timer = QTimer(self)
        self.repeat_timer.timeout.connect(self.send_data)

        # 连接信号
        self.check_repeat.stateChanged.connect(self.toggle_repeat)
        
        # --- 状态栏 ---
        ok_color = self.theme_colors['ansi_fg']['32'].name()
        self.status_msg = QLabel(f'<span style="color: {ok_color};">就绪</span>')
        self.status_msg.setFont(QFont("Microsoft YaHei", 9))
        self.statusBar().addWidget(self.status_msg)

        # 添加状态栏组件
        # 使用富文本设置连接状态，只改变状态部分的颜色
        self.status_connection = QLabel()
        self.status_connection.setFont(QFont("Microsoft YaHei", 9))
        # 初始状态为未连接
        self._update_status_connection_text(False)
        self.status_baud = QLabel("波特率: 115200")
        self.status_baud.setFont(QFont("Microsoft YaHei", 9))
        self.status_log = QLabel("日志文件: 未创建")
        self.status_log.setFont(QFont("Microsoft YaHei", 9))
        
        self.statusBar().addPermanentWidget(self.status_connection)
        self.statusBar().addPermanentWidget(QLabel("  ·  "))
        self.statusBar().addPermanentWidget(self.status_baud)
        self.statusBar().addPermanentWidget(QLabel("  ·  "))
        self.statusBar().addPermanentWidget(self.status_log)
        self.statusBar().addPermanentWidget(QLabel("  ·  "))
        
        # 版本号显示
        self.status_version = QLabel(f"版本: {VERSION}")
        self.status_version.setFont(QFont("Microsoft YaHei", 9))
        self.statusBar().addPermanentWidget(self.status_version)

        # 应用初始主题（必须在所有控件创建之后调用）
        self.apply_theme(self.current_theme)

    def toggle_repeat(self):
        """切换重复发送状态"""
        if self.check_repeat.isChecked():
            self.spin_interval.setEnabled(True)
        else:
            self.spin_interval.setEnabled(False)
            self.stop_repeat()

    def toggle_filter(self):
        """切换筛选功能"""
        self.filter_enabled = self.check_filter.isChecked()
        self.edit_filter.setEnabled(self.filter_enabled)
        self.combo_filter_mode.setEnabled(self.filter_enabled)
        # 切换「高亮显示」时不需要刷新已有内容（高亮只在新增文本时生效）
        # 关闭筛选时需要清除已有的高亮（简单处理：清不掉的旧高亮不处理）

    def update_filter_text(self, text):
        """更新筛选关键字"""
        self.filter_text = text

    def update_filter_mode(self, mode):
        """更新筛选模式"""
        self.filter_mode = mode

    def _match_filter(self, line):
        """检查行是否匹配筛选条件。
        支持逗号分隔的多关键字（OR 逻辑），以及三种匹配模式：
        - "包含": 大小写敏感子串匹配
        - "忽略大小写": 大小写不敏感子串匹配
        - "正则": 正则表达式匹配
        - "高亮显示": 不做筛选，始终显示（高亮在 append_text 中处理）

        HEX 显示模式下，行内容是 "48 65 6C" 形式的十六进制串，
        会把行和关键字都去掉空格再匹配，让用户输入 "4865" 或 "48 65" 都能命中。
        """
        if self.filter_mode == "高亮显示":
            return True
        keywords = [kw.strip() for kw in self.filter_text.split(',') if kw.strip()]
        if not keywords:
            return True

        # HEX 模式下规范化（去空格），便于用连续 hex 关键字匹配
        is_hex = self.check_hex_recv.isChecked()
        match_line = line.replace(' ', '') if is_hex else line

        for kw in keywords:
            if self.filter_mode == "包含":
                target_kw = kw.replace(' ', '') if is_hex else kw
                if target_kw in match_line:
                    return True
            elif self.filter_mode == "忽略大小写":
                target_kw = kw.replace(' ', '') if is_hex else kw
                if target_kw.lower() in match_line.lower():
                    return True
            elif self.filter_mode == "正则":
                try:
                    if re.search(kw, line):
                        return True
                except re.error:
                    # 正则表达式无效时跳过该关键字
                    continue
        return False

    def stop_repeat(self):
        """停止重复发送"""
        if self.repeat_timer.isActive():
            self.repeat_timer.stop()
            self.append_text("[系统]: 停止重复发送\n")
        self.btn_stop.setEnabled(False)
        self.check_repeat.setChecked(False)

    # ========== 主题相关方法 ==========

    def apply_theme(self, theme_name):
        """应用主题（light 或 dark）"""
        self.current_theme = theme_name
        self.theme_colors = dict(THEME_COLORS[theme_name])

        # 1. 全局 QSS（注入下拉箭头图片路径）
        if theme_name == 'dark':
            qss = DARK_QSS.replace('__ARROW_DARK__', self._arrow_dark_path)
            QApplication.instance().setStyleSheet(qss)
            self._set_titlebar_dark(True, color_hex='#282C34')
        else:
            qss = LIGHT_QSS.replace('__ARROW_LIGHT__', self._arrow_light_path)
            QApplication.instance().setStyleSheet(qss)
            self._set_titlebar_dark(False)

        # 同步菜单栏暗黑模式勾选状态
        if hasattr(self, 'act_dark_mode'):
            self.act_dark_mode.setChecked(theme_name == 'dark')

        # 2. 更新状态栏文字颜色（主题切换后刷新）
        self._refresh_status_connection()
        if hasattr(self, '_current_status_text'):
            self._set_status(self._current_status_text, self._current_status_level)


    def _set_titlebar_dark(self, dark=True, color_hex='#282C34'):
        """设置窗口标题栏/背景颜色以匹配主题（Windows / Linux / macOS）"""
        import platform
        system = platform.system()

        # ── 通用：设置应用级暗色调色板（非 Windows 系统的主要手段）──
        if dark:
            dark_palette = QApplication.instance().palette()
            dark_palette.setColor(QPalette.Window, QColor(color_hex))
            dark_palette.setColor(QPalette.WindowText, QColor(0xAB, 0xB2, 0xBF))
            dark_palette.setColor(QPalette.Base, QColor(0x2C, 0x31, 0x3C))
            dark_palette.setColor(QPalette.AlternateBase, QColor(0x21, 0x25, 0x2B))
            dark_palette.setColor(QPalette.Text, QColor(0xAB, 0xB2, 0xBF))
            dark_palette.setColor(QPalette.Button, QColor(0x2C, 0x31, 0x3C))
            dark_palette.setColor(QPalette.ButtonText, QColor(0xAB, 0xB2, 0xBF))
            QApplication.instance().setPalette(dark_palette)
        else:
            QApplication.instance().setPalette(QApplication.style().standardPalette())

        # ── Windows：DWM API 精确设置标题栏颜色 ──
        if system == 'Windows':
            try:
                import ctypes
                hwnd = int(self.winId())
                if not hwnd:
                    return

                if dark:
                    # Win11: DWMWA_CAPTION_COLOR (35) — 精确颜色
                    try:
                        r, g, b = int(color_hex[1:3], 16), int(color_hex[3:5], 16), int(color_hex[5:7], 16)
                        colorref = ctypes.c_uint((b << 16) | (g << 8) | r)
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            ctypes.wintypes.HWND(hwnd),
                            ctypes.c_uint(35),
                            ctypes.byref(colorref),
                            ctypes.sizeof(colorref))
                    except Exception:
                        pass
                    # Win10: DWMWA_USE_IMMERSIVE_DARK_MODE (19/20)
                    value = ctypes.c_int(1)
                    for attr in (20, 19):
                        try:
                            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                                    ctypes.wintypes.HWND(hwnd),
                                    ctypes.c_uint(attr),
                                    ctypes.byref(value),
                                    ctypes.sizeof(value)) == 0:
                                break
                        except Exception:
                            continue
                else:
                    # Win11: 重置标题栏颜色为系统默认
                    try:
                        none = ctypes.c_uint(0xFFFFFFFF)  # DWMWA_COLOR_NONE
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            ctypes.wintypes.HWND(hwnd),
                            ctypes.c_uint(35),
                            ctypes.byref(none),
                            ctypes.sizeof(none))
                    except Exception:
                        pass
                    # Win10: 取消暗黑模式
                    value = ctypes.c_int(0)
                    for attr in (20, 19):
                        try:
                            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                                    ctypes.wintypes.HWND(hwnd),
                                    ctypes.c_uint(attr),
                                    ctypes.byref(value),
                                    ctypes.sizeof(value)) == 0:
                                break
                        except Exception:
                            continue
            except Exception:
                pass

        # ── Linux：通过 X11 property 告知窗口管理器这是暗色应用 ──
        elif system == 'Linux':
            try:
                import ctypes
                x11 = ctypes.cdll.LoadLibrary('libX11.so.6')
                if not x11:
                    return
                display = x11.XOpenDisplay(None)
                if not display:
                    return
                try:
                    xwin = int(self.winId())
                    if xwin == 0:
                        return

                    # 获取 UTF8_STRING 类型原子
                    utf8_atom = x11.XInternAtom(display, ctypes.c_char_p(b'UTF8_STRING'), 0)
                    variant = ctypes.c_char_p(b'dark' if dark else b'light')
                    data_len = len(b'dark' if dark else b'light')

                    # 设置 _GTK_THEME_VARIANT hint (GNOME/GTK 环境)
                    gtk_atom = x11.XInternAtom(display, ctypes.c_char_p(b'_GTK_THEME_VARIANT'), 0)
                    x11.XChangeProperty(
                        display, ctypes.c_ulong(xwin),
                        gtk_atom, utf8_atom, 8,
                        ctypes.c_int(0),  # PropModeReplace
                        variant, data_len)
                    # 设置 _KDE_NET_WM_THEME_VARIANT hint (KDE Plasma 环境)
                    kde_atom = x11.XInternAtom(display, ctypes.c_char_p(b'_KDE_NET_WM_THEME_VARIANT'), 0)
                    x11.XChangeProperty(
                        display, ctypes.c_ulong(xwin),
                        kde_atom, utf8_atom, 8,
                        ctypes.c_int(0),
                        variant, data_len)
                    x11.XFlush(display)
                finally:
                    x11.XCloseDisplay(display)
            except Exception:
                pass

        # ── 确保 central widget 也获得暗色背景（QMainWindow QSS 不自动穿透）──
        central = self.centralWidget()
        if central is not None:
            central.setAutoFillBackground(True)
            bg_color = QColor(color_hex) if dark else QColor('#F5F5F5')
            palette = central.palette()
            palette.setColor(QPalette.Window, bg_color)
            central.setPalette(palette)

    def showEvent(self, event):
        """窗口显示时设置标题栏颜色（此时 winId 已有效）"""
        super().showEvent(event)
        if self.current_theme == 'dark':
            self._set_titlebar_dark(True, color_hex='#282C34')
        else:
            self._set_titlebar_dark(False)

    def _refresh_status_connection(self):
        """根据当前串口连接状态刷新状态栏文本颜色"""
        is_connected = (hasattr(self, 'transport') and self.transport
                        and self.transport.is_open)
        self._update_status_connection_text(is_connected)

    def _set_status(self, text, level="info"):
        """更新状态栏消息，按级别着色（颜色取自主题令牌）"""
        self._current_status_text = text
        self._current_status_level = level
        ok_color = self.theme_colors['ansi_fg']['32'].name()
        err_color = self.theme_colors['text_error'].name()
        if level == "ready":
            self.status_msg.setText(f'<span style="color: {ok_color};">{text}</span>')
        elif level == "error":
            self.status_msg.setText(f'<span style="color: {err_color};">{text}</span>')
        else:
            label_color = self.theme_colors['text_normal'].name()
            self.status_msg.setText(f'<span style="color: {label_color};">{text}</span>')

    def _update_status_connection_text(self, connected):
        """更新状态栏连接状态文字（主题感知）"""
        label_color = self.theme_colors['text_normal'].name()
        ok_color = self.theme_colors['ansi_fg']['32'].name()
        err_color = self.theme_colors['text_error'].name()
        if connected:
            self.status_connection.setText(
                f'<span style="color: {label_color};">连接状态：</span>'
                f'<span style="color: {ok_color};">已连接</span>'
            )
        else:
            self.status_connection.setText(
                f'<span style="color: {label_color};">连接状态：</span>'
                f'<span style="color: {err_color};">未连接</span>'
            )

    def toggle_theme(self):
        """切换主题"""
        new_theme = 'dark' if self.current_theme == 'light' else 'light'
        self.apply_theme(new_theme)
        self.save_config()
        # 同步已打开的 OTA 对话框主题
        if hasattr(self, '_ota_dialog') and self._ota_dialog is not None:
            try:
                if self._ota_dialog.isVisible():
                    self._apply_dialog_theme(self._ota_dialog)
            except RuntimeError:
                pass
        # 同步数据分析面板主题
        if hasattr(self, '_json_viewer_dlg') and self._json_viewer_dlg is not None:
            try:
                if self._json_viewer_dlg.isVisible():
                    self._json_viewer_dlg.set_theme(is_dark=(new_theme == 'dark'))
                    self._apply_dialog_theme(self._json_viewer_dlg)
            except RuntimeError:
                pass
        theme_display = "暗黑模式" if new_theme == 'dark' else "亮色模式"
        self.append_text(f"[系统]: 已切换至{theme_display}\n")
    def handle_baud_change(self, index):
        """处理波特率选择变化"""
        baud_text = self.combo_baud.itemText(index)
        if baud_text == "自定义":
            # 记录当前选中的波特率（用于取消时恢复）
            current_baud = self.combo_baud.currentText()
            if current_baud == "自定义":
                current_baud = '115200'  # 默认值

            # 弹出输入对话框，让用户输入波特率
            from PyQt5.QtWidgets import QInputDialog
            baud_rate, ok = QInputDialog.getInt(self, "自定义波特率", "请输入波特率:", 115200, 1, 1000000)

            # 断开信号连接，防止setCurrentIndex触发递归
            self.combo_baud.currentIndexChanged.disconnect(self.handle_baud_change)

            try:
                if ok:
                    baud_str = str(baud_rate)
                    # 检查是否已存在相同的波特率值
                    existing_index = self.combo_baud.findText(baud_str)

                    if existing_index >= 0:
                        # 如果已存在，直接选中已有的值
                        self.combo_baud.setCurrentIndex(existing_index)
                        self.custom_baud = int(baud_str)
                    else:
                        # 如果不存在，在"自定义"选项之前插入新的波特率
                        self.combo_baud.insertItem(index, baud_str)
                        # 选中新插入的波特率（此时index位置就是新插入的项）
                        self.combo_baud.setCurrentIndex(index)
                        self.custom_baud = baud_rate
                else:
                    # 如果用户取消输入，恢复到之前的波特率
                    self.combo_baud.setCurrentText(current_baud)
            finally:
                # 重新连接信号
                self.combo_baud.currentIndexChanged.connect(self.handle_baud_change)
        else:
            # 选择预置波特率，同步 custom_baud
            try:
                self.custom_baud = int(baud_text)
            except ValueError:
                pass

    # --- 功能函数 ---


    def _sync_button_text(self, text):
        """同步两套按钮文本，TCP Server 模式下自动翻译为侦听术语"""
        if self.connection_mode == 'tcp_server':
            if text == "打开连接":
                text = "侦听"
            elif text == "关闭连接":
                text = "停止侦听"
        self.btn_switch.setText(text)
        self.btn_switch_serial.setText(text)

    def on_mode_changed(self, index):
        mode_map = {0: 'serial', 1: 'udp', 2: 'tcp_client', 3: 'tcp_server'}
        new_mode = mode_map.get(index, 'serial')
        if hasattr(self, 'transport') and self.transport and self.transport.is_open:
            reply = QMessageBox.question(self, "切换模式",
                "当前连接正在使用，切换模式将断开连接。是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                reverse_map = {v: k for k, v in mode_map.items()}
                idx = reverse_map.get(self.connection_mode, 0)
                self.combo_mode.blockSignals(True)
                self.combo_mode.setCurrentIndex(idx)
                self.combo_mode.blockSignals(False)
                return
            self.btn_switch.setChecked(False); self.btn_switch_serial.setChecked(False)
            self.toggle_connection()
        self.connection_mode = new_mode
        self.stack_params.setCurrentIndex(index)
        is_serial = (new_mode == 'serial')
        # 按钮位置：串口模式下按钮在参数行内，其他模式在第一行
        self.btn_switch_serial.setVisible(is_serial)
        self.btn_more_settings_serial.setVisible(is_serial)
        self.btn_refresh_serial.setVisible(is_serial)
        self.btn_switch.setVisible(not is_serial)
        self.btn_more_settings.setVisible(not is_serial)
        # 刷新/IP按钮：仅 UDP/TCP Server 显示（串口用 btn_refresh_serial；TCP Client 无本地IP字段）
        has_local_ip = new_mode in ('udp', 'tcp_server')
        self.btn_refresh.setVisible(has_local_ip)
        self.btn_refresh.setText("获取本机IP")
        self.btn_more_settings.setEnabled(is_serial)
        self.btn_more_settings_serial.setEnabled(is_serial)
        self.check_rts.setVisible(is_serial)
        self.check_dtr.setVisible(is_serial)
        self.refresh_ports()
        self._sync_button_text("打开连接")  # 模式切换后刷新按钮文本（TCP Server → "侦听"）
        self.append_text(f"[系统]: 通信模式切换为 {new_mode}\n")

    def refresh_ports(self):
        """刷新可用的串口列表"""
        mode = getattr(self, 'connection_mode', 'serial')
        if mode == 'serial':
            self.combo_port.clear()
            ports = serial.tools.list_ports.comports()
            if ports:
                for port in ports:
                    self.combo_port.addItem(port.device)
                self.combo_port.setCurrentIndex(0)
            else:
                self.combo_port.addItem("无可用串口")
        else:
            try:
                hostname = socket.gethostname()
                local_ip = socket.gethostbyname(hostname)
                all_ips = socket.gethostbyname_ex(hostname)[2]
                if mode == 'udp' and hasattr(self, 'edit_udp_local_ip'):
                    self.edit_udp_local_ip.clear()
                    self.edit_udp_local_ip.addItems(all_ips)
                    self.edit_udp_local_ip.setCurrentText(local_ip)
                elif mode == 'tcp_server' and hasattr(self, 'edit_tcp_server_local_ip'):
                    self.edit_tcp_server_local_ip.clear()
                    self.edit_tcp_server_local_ip.addItems(all_ips)
                    self.edit_tcp_server_local_ip.setCurrentText(local_ip)
            except Exception:
                pass

    def toggle_connection(self):
        """打开或关闭通信连接（支持串口 / UDP / TCP Client / TCP Server）"""
        # 同步两套按钮的选中状态
        s = self.sender()
        if s is self.btn_switch_serial:
            self.btn_switch.blockSignals(True)
            self.btn_switch.setChecked(self.btn_switch_serial.isChecked())
            self.btn_switch.blockSignals(False)
        elif s is self.btn_switch:
            self.btn_switch_serial.blockSignals(True)
            self.btn_switch_serial.setChecked(self.btn_switch.isChecked())
            self.btn_switch_serial.blockSignals(False)
        if self.btn_switch.isChecked() or self.btn_switch_serial.isChecked():
            mode = self.connection_mode
            if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
                try:
                    self.read_thread.stop()
                    self.read_thread = None
                except Exception as e:
                    self.append_text(f"[错误]: 停止旧接收线程失败: {str(e)}\n")
            with QMutexLocker(self.serial_mutex):
                if hasattr(self, 'transport') and self.transport:
                    self.transport.close()
            # 确保 transport 始终有效（错误处理后可能已被置为 None）
            if not hasattr(self, 'transport') or self.transport is None:
                self.transport = TransportWrapper()
            try:
                connection_desc = ""
                if mode == 'serial':
                    port_name = self.combo_port.currentText()
                    if port_name == "无可用串口":
                        QMessageBox.warning(self, "错误", "没有检测到可用串口！")
                        self.btn_switch.setChecked(False); self.btn_switch_serial.setChecked(False)
                        return
                    try:
                        baud_rate = int(self.combo_baud.currentText())
                    except ValueError:
                        QMessageBox.warning(self, "错误", "无效的波特率！")
                        self.btn_switch.setChecked(False); self.btn_switch_serial.setChecked(False)
                        return
                    try:
                        data_bits = int(self.serial_data_bits) if hasattr(self, 'serial_data_bits') else 8
                    except (AttributeError, ValueError):
                        data_bits = 8
                    bytesize_map = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}
                    bytesize = bytesize_map.get(data_bits, serial.EIGHTBITS)
                    try:
                        stop_bits = self.serial_stop_bits if hasattr(self, 'serial_stop_bits') else '1'
                    except AttributeError:
                        stop_bits = '1'
                    stopbits_map = {'1': serial.STOPBITS_ONE, '1.5': serial.STOPBITS_ONE_POINT_FIVE, '2': serial.STOPBITS_TWO}
                    stopbits = stopbits_map.get(stop_bits, serial.STOPBITS_ONE)
                    try:
                        parity = self.serial_parity if hasattr(self, 'serial_parity') else 'None'
                    except AttributeError:
                        parity = 'None'
                    parity_map = {'None': serial.PARITY_NONE, 'Even': serial.PARITY_EVEN, 'Odd': serial.PARITY_ODD, 'Mark': serial.PARITY_MARK, 'Space': serial.PARITY_SPACE}
                    parity = parity_map.get(parity, serial.PARITY_NONE)
                    try:
                        flow_control = self.serial_flow_control if hasattr(self, 'serial_flow_control') else 'None'
                    except AttributeError:
                        flow_control = 'None'
                    xonxoff = (flow_control == 'Xon/Xoff')
                    rtscts = (flow_control == 'RTS/CTS')
                    dsrdtr = (flow_control == 'DSR/DTR')
                    self.transport.open_serial(port=port_name, baudrate=baud_rate, bytesize=bytesize, parity=parity, stopbits=stopbits, timeout=1.0, xonxoff=xonxoff, rtscts=rtscts, dsrdtr=dsrdtr)
                    try:
                        self.transport.rts = self.check_rts.isChecked()
                        self.transport.dtr = self.check_dtr.isChecked()
                    except Exception as e:
                        self.append_text(f"[警告]: 设置RTS/DTR状态失败: {str(e)}\n")
                    connection_desc = f"串口 {port_name}, 波特率 {baud_rate}"
                    self.combo_port.setEnabled(False)
                    self.combo_baud.setEnabled(False)
                    self.btn_refresh.setEnabled(False); self.btn_refresh_serial.setEnabled(False)
                    self.combo_mode.setEnabled(False)
                elif mode == 'udp':
                    local_ip = self.edit_udp_local_ip.currentText().strip() or '0.0.0.0'
                    local_port = self.edit_udp_local_port.value()
                    remote_ip = self.edit_udp_remote_ip.text().strip() or '192.168.1.1'
                    remote_port = self.edit_udp_remote_port.value()
                    self.transport.open_udp(local_ip, str(local_port), remote_ip, str(remote_port))
                    connection_desc = f"UDP {local_ip}:{local_port} -> {remote_ip}:{remote_port}"
                    self.combo_mode.setEnabled(False)
                    self.combo_port.setEnabled(False)
                    self.combo_baud.setEnabled(False)
                    self.btn_refresh.setEnabled(False); self.btn_refresh_serial.setEnabled(False)
                elif mode == 'tcp_client':
                    remote_ip = self.edit_tcp_remote_ip.text().strip() or '192.168.1.1'
                    remote_port = self.edit_tcp_remote_port.value()
                    self.transport.open_tcp_client(remote_ip, str(remote_port))
                    connection_desc = f"TCP Client -> {remote_ip}:{remote_port}"
                    self.combo_mode.setEnabled(False)
                    self.combo_port.setEnabled(False)
                    self.combo_baud.setEnabled(False)
                    self.btn_refresh.setEnabled(False); self.btn_refresh_serial.setEnabled(False)
                elif mode == 'tcp_server':
                    local_ip = self.edit_tcp_server_local_ip.currentText().strip() or '0.0.0.0'
                    local_port = self.edit_tcp_server_local_port.value()
                    self.transport.open_tcp_server(local_ip, str(local_port))
                    connection_desc = f"TCP Server {local_ip}:{local_port}"
                    self.combo_mode.setEnabled(False)
                    self.combo_port.setEnabled(False)
                    self.combo_baud.setEnabled(False)
                    self.btn_refresh.setEnabled(False); self.btn_refresh_serial.setEnabled(False)
                else:
                    QMessageBox.warning(self, "错误", f"未知的连接模式: {mode}")
                    self.btn_switch.setChecked(False); self.btn_switch_serial.setChecked(False)
                    return
                self.read_thread = TransportReadThread(self.transport, self.serial_mutex)
                self.read_thread.receive_data_signal.connect(self.handle_receive_data)
                self.read_thread.error_signal.connect(self.handle_read_error)
                # 如果 JSON 面板已打开，重新连接到新的 read_thread
                if hasattr(self, '_json_viewer_dlg') and self._json_viewer_dlg is not None:
                    try:
                        self.read_thread.receive_data_signal.connect(self._json_viewer_dlg.feed_raw_data)
                    except (TypeError, RuntimeError):
                        pass
                self.read_thread.start()
                self._sync_button_text("关闭连接")
                self.append_text(f"--- {connection_desc} 已打开 ---")
                self._update_status_connection_text(True)
                self.status_baud.setText(f"模式: {connection_desc}")
                self._set_status("就绪", "ready")
                self.error_state = False
            except Exception as e:
                error_msg = f"打开连接失败: {str(e)}"
                QMessageBox.critical(self, "连接打开失败", error_msg)
                self.btn_switch.setChecked(False); self.btn_switch_serial.setChecked(False)
                self.append_text(f"[错误]: {error_msg}\n")
                if hasattr(self, 'transport') and self.transport:
                    self.transport.close()
                if hasattr(self, 'read_thread') and self.read_thread:
                    try:
                        self.read_thread.stop()
                    except Exception:
                        pass
                self._set_status(f"打开失败: {error_msg}", "error")
                self.error_state = True
        else:
            if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
                self.batch_thread.stop()
                self.check_cycle_send.setChecked(False)
                self.append_text("[系统]: 批量发送已停止\n")
            # 停止重复发送定时器
            if hasattr(self, 'repeat_timer') and self.repeat_timer.isActive():
                self.repeat_timer.stop()
                self.btn_stop.setEnabled(False)
                self.check_repeat.setChecked(False)
                self.spin_interval.setEnabled(False)
            if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
                try:
                    self.read_thread.stop()
                except Exception as e:
                    self.append_text(f"[错误]: 停止接收线程失败: {str(e)}\n")
            if hasattr(self, 'transport') and self.transport:
                self.transport.close()
                self.append_text("--- 连接已关闭 ---")
            self._update_status_connection_text(False)
            self.status_baud.setText("波特率：115200")
            self._set_status("就绪", "ready")
            self.error_state = False
            self._sync_button_text("打开连接")
            self.combo_port.setEnabled(True)
            self.combo_baud.setEnabled(True)
            self.btn_refresh.setEnabled(True); self.btn_refresh_serial.setEnabled(True)
            self.combo_mode.setEnabled(True)

    def select_file_to_send(self):
        """选择要发送的文件"""
        from PyQt5.QtWidgets import QFileDialog
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "选择要发送的文件", 
            "", 
            "所有文件 (*.*);;文本文件 (*.txt);;二进制文件 (*.bin)"
        )
        
        if file_path:
            self.selected_file_path = file_path
            self.file_path_edit.setText(file_path)
            self.btn_send_file.setEnabled(True)
            self.append_text(f"[信息]: 已选择文件: {file_path}\n")

    def send_file(self):
        """发送文件数据"""
        # 检查串口状态
        if not hasattr(self, 'transport') or not self.transport or not self.transport.is_open:
            QMessageBox.warning(self, "警告", "请先打开连接！")
            return
        
        # 检查是否选择了文件
        if not hasattr(self, 'selected_file_path') or not self.selected_file_path:
            QMessageBox.warning(self, "警告", "请先选择要发送的文件！")
            return
        
        import os
        
        # 检查文件是否存在
        if not os.path.exists(self.selected_file_path):
            QMessageBox.warning(self, "警告", "文件不存在！")
            return
        
        # 获取文件大小
        file_size = os.path.getsize(self.selected_file_path)
        if file_size == 0:
            QMessageBox.warning(self, "警告", "文件为空！")
            return
        
        # 检查文件大小限制（最大10MB）
        max_file_size = 10 * 1024 * 1024  # 10MB
        if file_size > max_file_size:
            QMessageBox.warning(self, "警告", f"文件大小超过限制！最大支持 {max_file_size // (1024*1024)}MB，当前文件 {file_size // (1024*1024)}MB")
            return
        
        self.append_text(f"[信息]: 开始发送文件: {self.selected_file_path}")
        self.append_text(f"[信息]: 文件大小: {file_size} 字节\n")

        # 重置取消标志
        self.stop_file_send = False

        # 禁用发送按钮，防止重复发送
        self.btn_send_file.setEnabled(False)
        self.btn_send_file.setText("发送中…")
        
        # 使用线程池发送文件，避免阻塞UI
        worker = FileOperationWorker(self._send_file_worker, self.selected_file_path, file_size)
        worker.signals.result.connect(self.on_file_send_complete)
        worker.signals.error.connect(self.on_file_send_error)
        worker.signals.progress.connect(self.on_file_send_progress)
        self.thread_pool.start(worker)
    
    def _send_file_worker(self, signals, file_path, file_size):
        """文件发送工作线程"""
        import os
        
        try:
            with open(file_path, 'rb') as f:
                total_sent = 0
                chunk_size = 1024  # 每次发送1KB
                last_progress = 0
                
                while total_sent < file_size:
                    # 检查是否需要停止
                    if hasattr(self, 'stop_file_send') and self.stop_file_send:
                        raise Exception("文件发送已取消")
                    
                    # 读取数据块
                    data = f.read(chunk_size)
                    if not data:
                        break
                    
                    # 使用互斥锁保护串口操作
                    try:
                        with QMutexLocker(self.serial_mutex):
                            if hasattr(self, 'transport') and self.transport and self.transport.is_open:
                                self.transport.write(data)
                                self.transport.flush()
                    except Exception as mutex_error:
                        raise Exception(f"串口操作失败: {mutex_error}")
                    
                    total_sent += len(data)
                    
                    # 更新进度（每发送10%更新一次）
                    progress = int((total_sent / file_size) * 100)
                    if progress != last_progress and progress % 10 == 0:
                        last_progress = progress
                        signals.progress.emit(f"[进度]: 已发送 {progress}% ({total_sent}/{file_size} 字节)")
                
                return True, total_sent
        except Exception as e:
            return False, str(e)
    
    def on_file_send_complete(self, result):
        """文件发送完成回调"""
        success, data = result
        if success:
            self.append_text(f"[成功]: 文件发送完成，共发送 {data} 字节\n")
        else:
            self.append_text(f"[错误]: 文件发送失败: {data}\n")
        
        # 重新启用发送按钮
        self.btn_send_file.setEnabled(True)
        self.btn_send_file.setText("发送文件")
    
    def on_file_send_error(self, error):
        """文件发送错误回调"""
        self.append_text(f"[错误]: 文件发送错误: {error}\n")
        self.btn_send_file.setEnabled(True)
        self.btn_send_file.setText("发送文件")
    
    def on_file_send_progress(self, progress_msg):
        """文件发送进度回调"""
        self.append_text(f"{progress_msg}\n")

    def _validate_hex_input(self):
        """实时校验 HEX 发送输入框格式（主发送框 + 首/尾字段），无效时显示红色边框提示"""
        if not self.check_hex_send.isChecked():
            self.text_send.setStyleSheet("")
            self.text_ota.setStyleSheet("")
            self.text_tail.setStyleSheet("")
            return
        # 校验主发送框
        self._validate_hex_field(self.text_send, self.text_send.toPlainText())
        # 校验首字段
        self._validate_hex_field(self.text_ota, self.text_ota.text())
        # 校验尾字段
        self._validate_hex_field(self.text_tail, self.text_tail.text())

    def _validate_hex_field(self, widget, text):
        """校验单个字段的 HEX 格式，无效时设置红色边框"""
        if not text:
            widget.setStyleSheet("")
            return
        hex_str = text.replace(' ', '').replace('\n', '').replace('\r', '')
        if len(hex_str) % 2 != 0:
            widget.setStyleSheet(
                "border: 1px solid red; border-radius: 4px;")
            return
        try:
            bytes.fromhex(hex_str)
            widget.setStyleSheet("")
        except ValueError:
            widget.setStyleSheet(
                "border: 1px solid red; border-radius: 4px;")

    def send_data(self):
        """发送数据"""
        # 检查串口状态
        if not hasattr(self, 'transport') or not self.transport or not self.transport.is_open:
            # 如果是重复发送过程中连接断开，静默停止，不再弹窗
            if self.repeat_timer.isActive():
                self.stop_repeat()
                return
            QMessageBox.warning(self, "警告", "请先打开连接！")
            return

        # 获取发送内容
        content = self.text_send.toPlainText()
        if not content:
            QMessageBox.warning(self, "警告", "发送内容不能为空！")
            return

        # 获取首字段（保留空格）
        head_field = self.text_ota.text()
        
        # 获取尾字段（保留空格）
        tail_field = self.text_tail.text()

        encoding = self.combo_encoding.currentText()

        try:
            if self.check_hex_send.isChecked():
                # HEX 发送模式
                # 去除空格和换行，然后转换为字节
                hex_str = content.replace(' ', '').replace('\n', '').replace('\r', '')
                if len(hex_str) % 2 != 0:
                    QMessageBox.warning(self, "格式错误", "HEX 字符串长度必须是偶数！")
                    return
                # 验证是否为有效的十六进制字符
                try:
                    data = bytes.fromhex(hex_str)
                except ValueError as e:
                    QMessageBox.warning(self, "格式错误", f"无效的十六进制数据: {e}")
                    return
                # HEX 模式下，首/尾字段也作为 HEX 字符串解析
                if self.check_head_field.isChecked() and head_field:
                    try:
                        head_hex = head_field.replace(' ', '').replace('\n', '').replace('\r', '')
                        if head_hex:
                            if len(head_hex) % 2 != 0:
                                QMessageBox.warning(self, "格式错误", "首字段 HEX 字符串长度必须是偶数！")
                                return
                            head_data = bytes.fromhex(head_hex)
                            data = head_data + data
                    except ValueError as e:
                        QMessageBox.warning(self, "格式错误", f"首字段无效的十六进制数据: {e}")
                        return
                if self.check_tail_field.isChecked() and tail_field:
                    try:
                        tail_hex = tail_field.replace(' ', '').replace('\n', '').replace('\r', '')
                        if tail_hex:
                            if len(tail_hex) % 2 != 0:
                                QMessageBox.warning(self, "格式错误", "尾字段 HEX 字符串长度必须是偶数！")
                                return
                            tail_data = bytes.fromhex(tail_hex)
                            data = data + tail_data
                    except ValueError as e:
                        QMessageBox.warning(self, "格式错误", f"尾字段无效的十六进制数据: {e}")
                        return
                # 更新显示内容，包含首/尾字段的完整 hex
                content = ' '.join([f'{b:02X}' for b in data])
            else:
                # 文本发送模式
                # 如果勾选了首字段，添加到内容前面
                if self.check_head_field.isChecked() and head_field:
                    content = head_field + content
                # 如果勾选了尾字段，添加到内容后面
                if self.check_tail_field.isChecked() and tail_field:
                    content = content + tail_field
                # 处理回车换行
                if self.check_newline.isChecked():
                    content = content.rstrip('\r\n')  # 去除末尾的换行符，避免重复添加
                    data = (content + '\r\n').encode(encoding, errors='replace')
                else:
                    data = content.encode(encoding, errors='replace')

            # 计算并添加校验值
            checksum_type = self.combo_checksum.currentText()
            if checksum_type != "None":
                checksum = self.calculate_checksum(data, checksum_type)
                if checksum:
                    # 添加校验值到数据末尾
                    data_with_checksum = data + checksum
                    # 发送带校验值的数据（使用互斥锁保护）
                    try:
                        with QMutexLocker(self.serial_mutex):
                            bytes_sent = self.transport.write(data_with_checksum)
                        # 更新发送数据统计
                        self.tx_bytes += bytes_sent
                        self.label_tx_bytes.setText(f"发送字节: {self.tx_bytes}")
                        # 显示发送的内容和校验值
                        checksum_hex = ' '.join([f'{byte:02X}' for byte in checksum])
                        self.append_text(f"[发送]: {content}\n")
                        self.append_text(f"[校验]: {checksum_type} = {checksum_hex}\n")
                        mode_tag = "[HEX] " if self.check_hex_send.isChecked() else ""
                        self.append_text(f"[系统]: {mode_tag}已发送 {bytes_sent} 字节（含校验值）\n")
                    except serial.SerialException as e:
                        error_msg = f"发送数据失败: {e}"
                        QMessageBox.warning(self, "发送失败", error_msg)
                        self.append_text(f"[错误]: {error_msg}\n")
            else:
                # 发送原始数据（使用互斥锁保护）
                try:
                    with QMutexLocker(self.serial_mutex):
                        bytes_sent = self.transport.write(data)
                    # 更新发送数据统计
                    self.tx_bytes += bytes_sent
                    self.label_tx_bytes.setText(f"发送字节: {self.tx_bytes}")
                    # 在接收区显示发送的内容，让用户能看到发送状态
                    self.append_text(f"[发送]: {content}\n")
                    mode_tag = "[HEX] " if self.check_hex_send.isChecked() else ""
                    self.append_text(f"[系统]: {mode_tag}已发送 {bytes_sent} 字节\n")
                except serial.SerialException as e:
                    error_msg = f"发送数据失败: {e}"
                    QMessageBox.warning(self, "发送失败", error_msg)
                    self.append_text(f"[错误]: {error_msg}\n")

            # 记录发送历史（去重连续相同条目）
            raw_text = self.text_send.toPlainText()
            if raw_text and (len(self.send_history) == 0 or self.send_history[0] != raw_text):
                self.send_history.appendleft(raw_text)
            self.history_index = -1  # 发送后重置历史浏览位置

            # 处理重复发送
            if self.check_repeat.isChecked():
                # 只有在定时器未运行时才启动，避免重复启动
                if not self.repeat_timer.isActive():
                    # 启动定时器
                    interval = self.spin_interval.value()
                    self.repeat_timer.start(interval)
                    self.append_text(f"[系统]: 开始重复发送，间隔 {interval}ms\n")
                    self.btn_stop.setEnabled(True)  # 启用停止按钮
            else:
                # 停止定时器
                if self.repeat_timer.isActive():
                    self.repeat_timer.stop()
                    self.append_text("[系统]: 停止重复发送\n")
                self.btn_stop.setEnabled(False)  # 禁用停止按钮
                
        except serial.SerialException as e:
            error_msg = f"串口发送失败: {str(e)}"
            QMessageBox.warning(self, "发送失败", error_msg)
            self.append_text(f"[错误]: {error_msg}\n")
        except ValueError as e:
            error_msg = f"数据格式错误: {str(e)}"
            QMessageBox.warning(self, "发送失败", error_msg)
            self.append_text(f"[错误]: {error_msg}\n")
        except IOError as e:
            error_msg = f"I/O错误: {str(e)}"
            QMessageBox.warning(self, "发送失败", error_msg)
            self.append_text(f"[错误]: {error_msg}\n")
        except Exception as e:
            error_msg = f"发送失败: {str(e)}"
            QMessageBox.warning(self, "发送失败", error_msg)
            self.append_text(f"[错误]: {error_msg}\n")
            # 确保停止按钮状态正确
            if self.repeat_timer.isActive():
                self.repeat_timer.stop()
                self.append_text("[系统]: 停止重复发送\n")
            self.btn_stop.setEnabled(False)  # 禁用停止按钮

    def process_ansi_colors(self, text):
        """处理ANSI颜色转义序列，返回文本和格式信息"""
        # 使用主题感知的 ANSI 颜色映射
        ansi_colors = self.theme_colors['ansi_fg']
        ansi_bg_colors = self.theme_colors['ansi_bg']
        default_fg = self.theme_colors['ansi_default_fg']

        # 处理ANSI转义序列
        result = []
        current_format = QTextCharFormat()
        current_format.setForeground(default_fg)  # 根据主题设置默认前景色

        last_end = 0
        for match in _ANSI_PATTERN.finditer(text):
            # 添加匹配前的文本
            plain_text = text[last_end:match.start()]
            if plain_text:
                # 处理控制字符（用列表+join 替代字符串拼接，避免 O(n²)）
                processed_plain = self._escape_control_chars(plain_text)
                result.append((processed_plain, QTextCharFormat(current_format)))

            # 处理ANSI代码
            codes = match.group(1)
            if codes:
                # 完整的ANSI转义序列
                code_list = codes.split(';')
                for code in code_list:
                    if code == '0':
                        # 重置所有样式
                        current_format = QTextCharFormat()
                        current_format.setForeground(default_fg)  # 重置为当前主题默认前景色
                    elif code in ansi_colors:
                        current_format.setForeground(ansi_colors[code])
                    elif code in ansi_bg_colors:
                        current_format.setBackground(ansi_bg_colors[code])
                    elif code == '1':
                        current_format.setFontWeight(QFont.Bold)
                    elif code == '4':
                        current_format.setFontUnderline(True)
            else:
                # 单独的\x1B字符
                result.append((f'\\x1B', QTextCharFormat(current_format)))

            last_end = match.end()

        # 添加剩余的文本
        remaining_text = text[last_end:]
        if remaining_text:
            processed_remaining = self._escape_control_chars(remaining_text)
            result.append((processed_remaining, QTextCharFormat(current_format)))

        return result

    @staticmethod
    def _escape_control_chars(text):
        """把 ASCII 控制字符（除 \\r \\n \\t）转义为 \\xXX 形式。

        使用字典查表 + 列表拼接，比逐字符 += 字符串拼接快得多（避免 O(n²)）。
        """
        if not text:
            return text
        # 快速路径：不含控制字符时直接返回原串
        if all(ord(c) >= 32 or c in '\r\n\t' for c in text):
            return text
        parts = []
        for char in text:
            esc = _CONTROL_CHARS_ESCAPE.get(char)
            if esc is not None:
                parts.append(esc)
            else:
                parts.append(char)
        return ''.join(parts)

    def handle_read_error(self, error_msg):
        """处理传输读取错误"""
        # 先保存错误信息，后续在UI线程中显示
        self.error_state = True  # 设置错误状态标志
        
        # 使用 try-except 包裹锁操作，防止锁获取失败导致程序崩溃
        try:
            # 在锁内只执行必要的串口关闭操作（避免死锁）
            with QMutexLocker(self.serial_mutex):
                # 关闭连接（最重要的操作，先执行）
                if hasattr(self, 'transport') and self.transport:
                    try:
                        if self.transport.is_open:
                            self.transport.close()
                    except Exception as e:
                        pass
        except Exception as e:
            # 锁获取失败，尝试直接关闭连接
            if hasattr(self, 'transport') and self.transport:
                try:
                    if self.transport.is_open:
                        self.transport.close()
                except Exception:
                    pass
        
        # 在锁外停止线程（避免死锁）
        try:
            # 停止批量发送线程
            if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
                self.batch_thread.stop()

            # 停止重复发送定时器
            if hasattr(self, 'repeat_timer') and self.repeat_timer.isActive():
                self.repeat_timer.stop()

            # 停止接收线程
            if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
                try:
                    self.read_thread.stop()
                    self.read_thread = None  # 清空线程引用
                except Exception as e:
                    pass
        except Exception as e:
            # 线程停止失败不影响主要流程
            pass
        
        # 在锁外执行UI更新（避免死锁）
        try:
            QMessageBox.critical(self, "读取错误", error_msg)
            self.append_text(f"[错误]: {error_msg}\n")
            
            # 更新批量发送状态
            if hasattr(self, 'check_cycle_send') and self.check_cycle_send.isChecked():
                self.check_cycle_send.setChecked(False)
                self.append_text("[系统]: 批量发送已停止\n")

            # 更新重复发送状态
            if hasattr(self, 'check_repeat') and self.check_repeat.isChecked():
                self.check_repeat.setChecked(False)
                self.btn_stop.setEnabled(False)
                self.spin_interval.setEnabled(False)

            # 更新UI状态
            self._sync_button_text("打开连接")
            self.btn_switch.setChecked(False); self.btn_switch_serial.setChecked(False)
            self.combo_port.setEnabled(True)
            self.combo_baud.setEnabled(True)
            self.btn_refresh.setEnabled(True); self.btn_refresh_serial.setEnabled(True)
            
            # 更新状态栏
            self._update_status_connection_text(False)
            self.status_baud.setText("波特率: 115200")
            self._set_status(f"连接读取错误: {error_msg}", "error")
        except Exception as e:
            # UI更新失败不影响主要流程
            pass
        
        # 错误状态保持为True，直到用户重新连接串口成功
    
    def cleanup_resources(self):
        """清理所有资源"""
        # 停止定时器
        if hasattr(self, 'repeat_timer') and self.repeat_timer and self.repeat_timer.isActive():
            self.repeat_timer.stop()
        # 停止日志定时刷盘定时器，并做最后一次落盘
        if hasattr(self, '_log_fsync_timer') and self._log_fsync_timer:
            self._log_fsync_timer.stop()
        self._fsync_log_file()
        # 停止接收区刷新/跟底相关定时器
        if hasattr(self, '_recv_flush_timer') and self._recv_flush_timer:
            self._recv_flush_timer.stop()
        if hasattr(self, '_recv_follow_timer') and self._recv_follow_timer:
            self._recv_follow_timer.stop()
        
        # 停止批量发送线程（在锁外停止，避免死锁）
        if hasattr(self, 'batch_thread') and self.batch_thread and self.batch_thread.isRunning():
            try:
                self.batch_thread.stop()
            except Exception as e:
                print(f"停止批量发送线程失败: {e}")
            finally:
                self.batch_thread = None
        
        # 停止接收线程并关闭连接（需要互斥锁保护）
        with QMutexLocker(self.serial_mutex):
            # 停止接收线程
            if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
                try:
                    self.read_thread.stop()
                except Exception as e:
                    print(f"停止接收线程失败: {e}")
                finally:
                    self.read_thread = None
            
            # 关闭连接
            if hasattr(self, 'transport') and self.transport:
                try:
                    if self.transport.is_open:
                        self.transport.close()
                except Exception as e:
                    print(f"关闭连接失败: {e}")
                finally:
                    self.transport = None
        
        # 关闭日志文件
        if hasattr(self, 'current_log_file') and self.current_log_file:
            try:
                self.current_log_file.close()
            except Exception as e:
                print(f"关闭日志文件失败: {e}")
            finally:
                self.current_log_file = None
        
        # 清理线程池（设置超时时间，避免程序无法退出）
        if hasattr(self, 'thread_pool') and self.thread_pool:
            # 先尝试温和地清理
            self.thread_pool.clear()
            
            # 设置超时时间为5秒
            if not self.thread_pool.waitForDone(5000):
                print("线程池清理超时，尝试强制终止...")
                # 强制终止所有线程
                try:
                    # 直接设置线程池为过期状态
                    self.thread_pool.clear()
                    # 再次等待一小段时间
                    if not self.thread_pool.waitForDone(1000):
                        print("线程池强制终止失败，部分任务可能未完成")
                except Exception as e:
                    print(f"强制终止线程池时出错: {e}")
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        # 关闭数据分析面板（独立窗口，需显式关闭）
        if hasattr(self, '_json_viewer_dlg') and self._json_viewer_dlg is not None:
            try:
                self._json_viewer_dlg.close()
            except RuntimeError:
                pass
            self._json_viewer_dlg = None
        # 关闭 OTA 升级控制中心：必须在 save_config 之前关闭，
        # 触发其 closeEvent → _save_settings 把最新设置（含 OTA 指令）写入文件，
        # 否则主窗口 save_config 保留的会是旧值，导致 OTA 指令修改丢失
        if hasattr(self, '_ota_dialog') and self._ota_dialog is not None:
            try:
                self._ota_dialog.close()
            except RuntimeError:
                pass
            self._ota_dialog = None
        # 保存配置
        self.save_config()
        # 清理 PID 配置文件（已同步到基础配置，PID 文件可删除）
        try:
            if os.path.exists(self.config_file):
                os.remove(self.config_file)
        except Exception:
            pass
        # 清理所有资源（cleanup_resources 已经包含了所有必要的清理）
        self.cleanup_resources()

        # 接受关闭事件
        event.accept()

    def _build_log_entry(self, timestamp, payload):
        """截断 payload 并拼接时间戳，返回 (截断后的payload, log_entry)。

        截断时扣除时间戳长度，使最终 log_entry 真正满足 MAX_LOG_ENTRY_LENGTH 上限
        （原实现只截断 payload，拼接时间戳后仍会超限）。返回的截断 payload 供显示使用，
        保证显示与日志内容一致。
        """
        suffix = "...(截断)"
        max_payload = self.MAX_LOG_ENTRY_LENGTH - len(timestamp)
        if len(payload) > max_payload:
            cut = max(max_payload - len(suffix), 0)
            payload = payload[:cut] + suffix
        return payload, timestamp + payload

    def handle_receive_data(self, data):
        """处理接收到的数据"""
        try:
            # 数据包计数（整个数据包计一次；字节数在下方/分段处理中累加）
            self.packets += 1
            self.label_packets.setText(f"数据包: {self.packets}")

            # 统一取一次时间戳，分段处理时复用，避免同一数据包出现多个不同时间戳
            timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")

            # 限制单次处理的数据大小，防止缓冲区溢出
            max_single_process = 1024 * 1024  # 1MB
            if len(data) > max_single_process:
                # 大数据包分段处理（每段都会显示到界面并累加字节数）
                chunks = [data[i:i+max_single_process] for i in range(0, len(data), max_single_process)]
                for chunk in chunks:
                    self._handle_receive_data_chunk(chunk, timestamp)
                return

            # 更新接收字节统计
            self.rx_bytes += len(data)
            self.label_rx_bytes.setText(f"接收字节: {self.rx_bytes}")
            
            if self.check_hex_recv.isChecked():
                # HEX 显示
                hex_str = ' '.join([f'{byte:02X}' for byte in data])
                # 拼接并限制整体日志长度（含时间戳），返回截断后的 hex_str 供显示
                hex_str, log_entry = self._build_log_entry(timestamp, hex_str)
                # 添加到日志缓冲区（deque自动管理大小）
                self.log_buffer.append(log_entry)
                
                # 显示到界面（限制频率）
                self._update_receive_display(hex_str, timestamp)
                
                # 自动保存
                if self.auto_save_enabled and self.current_log_file:
                    self.auto_save_data(log_entry + '\n')
            else:
                # 文本显示，处理特殊字符和转义序列
                # 使用用户选择的编码进行解码
                encoding = self.combo_encoding.currentText()
                try:
                    text = data.decode(encoding, errors='replace')
                except LookupError:
                    # 如果编码名称无效，回退到UTF-8
                    encoding = 'UTF-8'
                    text = data.decode('utf-8', errors='replace')
                
                # 拼接并限制整体日志长度（含时间戳），返回截断后的 text 供显示
                text, log_entry = self._build_log_entry(timestamp, text)
                # 添加到日志缓冲区（deque自动管理大小）
                self.log_buffer.append(log_entry)
                
                # 显示到界面（限制频率）
                self._update_receive_display(text, timestamp)
                
                # 自动保存
                if self.auto_save_enabled and self.current_log_file:
                    self.auto_save_data(log_entry + '\n')
        except Exception as e:
            error_msg = f"[解码错误]: {e}"
            # 添加到日志缓冲区（deque自动管理大小）
            timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")
            log_entry = timestamp + error_msg
            self.log_buffer.append(log_entry)
            # 显示到界面
            self.append_text(error_msg)
            # 自动保存
            if self.auto_save_enabled and self.current_log_file:
                self.auto_save_data(log_entry + '\n')
    
    def _handle_receive_data_chunk(self, data, timestamp):
        """处理数据块（用于大数据包分段处理）。

        timestamp 由调用方传入并复用，保证同一数据包各分段时间戳一致。
        """
        try:
            # 更新接收数据统计
            self.rx_bytes += len(data)
            self.label_rx_bytes.setText(f"接收字节: {self.rx_bytes}")

            if self.check_hex_recv.isChecked():
                hex_str = ' '.join([f'{byte:02X}' for byte in data])
                hex_str, log_entry = self._build_log_entry(timestamp, hex_str)
                self.log_buffer.append(log_entry)
                # 显示到界面（与主路径一致，限制频率）
                self._update_receive_display(hex_str, timestamp)
                if self.auto_save_enabled and self.current_log_file:
                    self.auto_save_data(log_entry + '\n')
            else:
                encoding = self.combo_encoding.currentText()
                try:
                    text = data.decode(encoding, errors='replace')
                except LookupError:
                    text = data.decode('utf-8', errors='replace')
                text, log_entry = self._build_log_entry(timestamp, text)
                self.log_buffer.append(log_entry)
                # 显示到界面（与主路径一致，限制频率）
                self._update_receive_display(text, timestamp)
                if self.auto_save_enabled and self.current_log_file:
                    self.auto_save_data(log_entry + '\n')
        except Exception as e:
            # 分段处理异常不再静默吞掉，记录到日志缓冲区并提示用户，
            # 与主路径行为保持一致，便于排查数据丢失问题
            error_msg = f"[分段解码错误]: {e}"
            log_entry = timestamp + error_msg
            self.log_buffer.append(log_entry)
            self.append_text(error_msg)
            if self.auto_save_enabled and self.current_log_file:
                self.auto_save_data(log_entry + '\n')
    
    def _update_receive_display(self, text, timestamp):
        """更新接收区显示（限制更新频率，缓冲数据避免丢失）"""
        # 将数据添加到待显示缓冲区
        self._pending_display_data.append((text, timestamp))
        # 启动兜底定时器：即使数据停止，最后一笔也能在 50ms 内刷出
        self._recv_flush_timer.start()

        # 至少间隔20ms更新一次UI（与读取线程20ms休眠匹配）
        if time.time() - self._last_display_update_time < 0.02:
            return
        self._flush_pending_display()

    @staticmethod
    def _utf16_len(s):
        """返回字符串在 Qt 文档中占用的 QChar 数（UTF-16 code unit 数）。

        Qt 光标位置按 UTF-16 code unit 计数，BMP 外字符（>= 0x10000，
        如 emoji、部分 CJK 扩展字）占 2 个 code unit，其余占 1 个。
        直接用 Python len() 会导致高亮位置偏移。
        """
        return sum(2 if ord(c) >= 0x10000 else 1 for c in s)

    def _apply_keyword_highlight(self, cursor, start_pos, text, hl_fmt):
        """对 text 中出现的筛选关键字叠加背景色高亮（不区分大小写）。

        位置计算使用 UTF-16 code unit 长度，与 Qt 光标位置一致，
        避免 BMP 外字符导致高亮偏移。
        """
        if not text:
            return
        keywords = [kw.strip() for kw in self.filter_text.split(',') if kw.strip()]
        if not keywords:
            return
        text_lower = text.lower()
        for kw in keywords:
            kw_lower = kw.lower()
            kw_qt_len = self._utf16_len(kw)
            offset = 0
            while True:
                idx = text_lower.find(kw_lower, offset)
                if idx == -1:
                    break
                prefix_qt_len = self._utf16_len(text[:idx])
                cursor.setPosition(start_pos + prefix_qt_len)
                cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, kw_qt_len)
                cursor.mergeCharFormat(hl_fmt)
                offset = idx + len(kw)

    def _flush_pending_display(self):
        """把节流缓冲区里的待显示数据合并渲染到接收区。

        使用单一编辑块（beginEditBlock/endEditBlock）批量插入所有待显示行，
        循环内不再单独裁剪/滚动，循环结束后统一裁剪一次 + 滚动一次，
        避免节流失效和界面卡顿。
        """
        if not self._pending_display_data:
            return
        self._last_display_update_time = time.time()
        pending = self._pending_display_data
        self._pending_display_data = []

        show_timestamp = self.act_timestamp.isChecked()
        highlight_mode = self.filter_enabled and self.filter_mode == "高亮显示"
        tc = self.theme_colors
        hl_fmt = QTextCharFormat()
        hl_fmt.setBackground(tc['highlight_bg'])

        cursor = self.text_recv.textCursor()
        cursor.beginEditBlock()
        try:
            for text_item, ts_item in pending:
                # 按换行符分割数据，确保每行一个时间戳
                # 先去除\r（串口设备常用\r\n结尾），再分割
                lines = text_item.replace('\r', '').split('\n')
                line_count = len(lines)

                for i, line in enumerate(lines):
                    # 跳过空行（连续换行或开头换行的情况）
                    if not line:
                        continue

                    # 筛选过滤：仅显示匹配关键字的行（不影响日志记录和自动保存）
                    # 高亮显示模式下 _match_filter 恒为 True，跳过调用以省时
                    if self.filter_enabled and self.filter_text and not highlight_mode:
                        if not self._match_filter(line):
                            continue

                    # 处理ANSI颜色转义序列和控制字符
                    formatted_segments = self.process_ansi_colors(line)

                    cursor.movePosition(QTextCursor.End)

                    # 时间戳
                    if show_timestamp:
                        if cursor.atBlockStart():
                            cursor.insertText(ts_item)
                        else:
                            cursor.insertText('\n' + ts_item)
                        cursor.movePosition(QTextCursor.End)

                    # 记录本行格式化文本起始位置（用于高亮）
                    hl_start = cursor.position() if highlight_mode else 0

                    # 插入格式化文本段
                    for seg_text, seg_fmt in formatted_segments:
                        cursor.movePosition(QTextCursor.End)
                        cursor.setCharFormat(seg_fmt)
                        cursor.insertText(seg_text)

                    # 高亮显示模式：对刚插入的文本叠加背景色
                    if highlight_mode:
                        full_text = ''.join(seg_text for seg_text, _ in formatted_segments)
                        self._apply_keyword_highlight(cursor, hl_start, full_text, hl_fmt)

                    # 如果不是最后一行，添加换行符
                    if i < line_count - 1 and line:
                        cursor.movePosition(QTextCursor.End)
                        cursor.insertText('\n')
        finally:
            cursor.endEditBlock()

        # 循环结束后统一裁剪一次 + 滚动一次（避免逐行裁剪/滚动导致节流失效）
        self._trim_recv_buffer()
        if not self._recv_user_reading:
            self.text_recv.ensureCursorVisible()
            self._recv_auto_scroll_to_bottom()

    def _on_recv_wheel(self, event):
        """接收区滚轮：向上翻离底部超过阈值时暂停跟底；翻回底部立即恢复。

        阈值取约 3 行（用滚动条 singleStep 估算），避免在最新区随手搓滚轮误触发；
        查阅中继续滚动会重新计时，滚回底部附近则立即恢复（不必等满 6 秒）。
        """
        delta = event.angleDelta().y()            # >0 向上翻（看更早内容）
        sb = self.text_recv.verticalScrollBar()
        dist_from_bottom = sb.maximum() - sb.value()
        line = sb.singleStep() if sb.singleStep() > 0 else 20
        threshold = max(line * 5, 40)             # 距底部约 5 行以内视为"在底部"

        # 已在底部附近且继续向下滚 → 用户回到最新处，立即恢复跟底
        if dist_from_bottom <= threshold and delta < 0:
            self._recv_user_reading = False
            self._recv_follow_timer.stop()
            return

        # 向上翻离开底部 → 进入查阅（暂停跟底）；查阅中继续滚动 → 重新计时
        if delta > 0 or self._recv_user_reading:
            self._recv_user_reading = True
            self._recv_follow_timer.start()

    def _on_recv_follow_resume(self):
        """滚轮静止 6 秒后：恢复自动跟底，并立即刷新到最新内容。"""
        self._recv_user_reading = False
        # 查阅期间可能跳过了裁剪，恢复后把行数压回上限再跟底
        self._trim_recv_buffer(force=True)
        self._recv_auto_scroll_to_bottom()

    def _recv_auto_scroll_to_bottom(self):
        """接收区自动跟底：仅在用户未在滚动查看时才滚动到底部。"""
        if self._recv_user_reading:
            return
        sb = self.text_recv.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _trim_recv_buffer(self, force=False):
        """裁剪接收区旧行，保持行数在上限内。

        用户滚动查阅时跳过裁剪，避免抽走正在看的行；但超过 3 倍硬上限时强制裁剪防 OOM。
        force=True 时无视查阅状态，把行数压回 MAX_DISPLAY_LINES（用于恢复跟底后清理）。
        """
        block_count = self.text_recv.document().blockCount()
        if block_count < self.MAX_DISPLAY_LINES:
            return
        if self._recv_user_reading and not force and block_count < self.MAX_DISPLAY_LINES * 3:
            return  # 查阅中，保留用户正在看的内容
        cursor = self.text_recv.textCursor()
        cursor.beginEditBlock()
        # 删除前面的10%内容（约500行），保留大部分内容
        lines_to_remove = max(100, int(self.MAX_DISPLAY_LINES * 0.1))
        if force:
            # 强制裁剪：多删一些，把行数压回上限
            lines_to_remove = max(lines_to_remove, block_count - self.MAX_DISPLAY_LINES)
        cursor.movePosition(QTextCursor.Start)
        for _ in range(lines_to_remove):
            cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor)
            if not cursor.atEnd():
                cursor.removeSelectedText()
        cursor.endEditBlock()
        # 裁剪后文档字符位置整体前移，把显示光标重置到末尾，
        # 避免后续操作命中已被删除的区域
        self.text_recv.moveCursor(QTextCursor.End)

    def append_formatted_text(self, formatted_segments):
        """向接收区追加带格式的文本，并自动滚动到底部"""
        # 行数超限时裁剪旧行（查阅中由 _trim_recv_buffer 决定是否跳过）
        self._trim_recv_buffer()

        # 开始一个编辑块，提高性能
        cursor = self.text_recv.textCursor()
        cursor.beginEditBlock()

        # 移动到文本末尾
        cursor.movePosition(QTextCursor.End)

        # 记录插入起始位置（用于后续高亮）
        hl_start = cursor.position()

        # 添加带格式的文本段
        for text, format in formatted_segments:
            cursor.movePosition(QTextCursor.End)
            cursor.setCharFormat(format)
            cursor.insertText(text)

        # 结束编辑块
        cursor.endEditBlock()

        # 高亮显示模式：对刚插入的文本叠加背景色（不区分大小写）
        if self.filter_enabled and self.filter_mode == "高亮显示":
            full_text = ''.join(text for text, fmt in formatted_segments)
            if full_text:
                tc = self.theme_colors
                hl_fmt = QTextCharFormat()
                hl_fmt.setBackground(tc['highlight_bg'])
                self._apply_keyword_highlight(cursor, hl_start, full_text, hl_fmt)

        # 确保文本可见（仅当用户未在滚动查看时，避免打断查阅）
        if not self._recv_user_reading:
            self.text_recv.ensureCursorVisible()
            # 滚动到底部
            self._recv_auto_scroll_to_bottom()

    def append_text(self, text):
        """向接收区追加文本，并自动滚动到底部"""
        # 去除文本末尾多余的换行符（调用者可能已带\n），统一由本函数添加
        display_text = text.rstrip('\n')
        
        # 只有系统消息、错误消息和发送消息才添加到日志缓冲区
        if "[系统]:" in text or "[错误]:" in text or "[发送]:" in text:
            timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")
            log_entry = timestamp + text
            # 限制日志条目长度
            if len(log_entry) > self.MAX_LOG_ENTRY_LENGTH:
                log_entry = log_entry[:self.MAX_LOG_ENTRY_LENGTH] + "...(截断)"
            # 添加到日志缓冲区（deque自动管理大小）
            self.log_buffer.append(log_entry)
        
        # 获取光标
        cursor = self.text_recv.textCursor()

        # 行数超限时裁剪旧行（查阅中由 _trim_recv_buffer 决定是否跳过）
        self._trim_recv_buffer()

        # 移动到末尾
        cursor.movePosition(QTextCursor.End)
        
        # 根据消息类型设置不同的颜色
        tc = self.theme_colors
        if "[发送]:" in text:
            format = QTextCharFormat()
            format.setForeground(tc['text_send'])
            cursor.setCharFormat(format)
        elif "[系统]:" in text:
            format = QTextCharFormat()
            format.setForeground(tc['text_system'])
            cursor.setCharFormat(format)
        elif "[错误]:" in text:
            format = QTextCharFormat()
            format.setForeground(tc['text_error'])
            cursor.setCharFormat(format)
        else:
            format = QTextCharFormat()
            format.setForeground(tc['text_normal'])
            cursor.setCharFormat(format)
        
        # 插入文本
        cursor.insertText(display_text + '\n')

        # 高亮显示模式：对刚插入的文本叠加背景色（不区分大小写）
        if self.filter_enabled and self.filter_mode == "高亮显示":
            # 用插入前后的光标位置差值定位，避免 Python len() 与 Qt position 在
            # BMP 外字符上不一致导致高亮偏移
            end_pos = cursor.position()
            insert_start = end_pos - self._utf16_len(display_text) - 1  # -1 for \n
            hl_fmt = QTextCharFormat()
            hl_fmt.setBackground(tc['highlight_bg'])
            self._apply_keyword_highlight(cursor, insert_start, display_text, hl_fmt)

        # 确保文本可见（仅当用户未在滚动查看时，避免打断查阅）
        if not self._recv_user_reading:
            self.text_recv.ensureCursorVisible()
            # 滚动到底部
            self._recv_auto_scroll_to_bottom()

    def clear_recv_area(self):
        """清空接收区"""
        # 确认对话框，防止误操作丢失数据
        reply = QMessageBox.question(
            self, "确认清空", "确定要清空接收区吗？\n所有已接收的数据将丢失。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.text_recv.clear()
        # 清空日志缓冲区
        self.log_buffer.clear()
        # 重置滚动跟底状态（清空后恢复自动跟底，并丢弃待显示缓冲）
        self._recv_user_reading = False
        self._recv_follow_timer.stop()
        self._recv_flush_timer.stop()
        self._pending_display_data = []
        # 添加系统消息
        self.append_text("[系统]: 接收区已清空")
    
    def clear_send_area(self):
        """清空发送区"""
        self.text_send.clear()
        # 添加系统消息
        self.append_text("[系统]: 发送区已清空")

    def is_valid_path(self, path):
        """验证路径是否合法，防止路径注入攻击"""
        import os
        try:
            if not path or not isinstance(path, str):
                return False
            
            # 规范化路径（跨平台兼容）
            normalized_path = os.path.normpath(path)
            
            # 获取规范化后的路径组件（不包括空字符串）
            parts = [p for p in normalized_path.split(os.sep) if p]
            
            # 检查是否包含路径遍历组件 '..'
            if '..' in parts:
                print(f"路径包含遍历组件 '..': {normalized_path}")
                return False
            
            # 确保规范化后的路径是绝对路径
            abs_path = os.path.abspath(normalized_path)
            
            # 获取允许的目录列表（跨平台兼容）
            allowed_dirs = []
            
            # 程序运行目录
            base_dir = os.path.abspath(os.getcwd())
            allowed_dirs.append(base_dir)
            
            # 用户文档目录（跨平台兼容）
            user_documents = os.path.join(os.path.expanduser("~"), "Documents")
            allowed_dirs.append(user_documents)
            
            # 用户桌面目录
            user_desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            allowed_dirs.append(user_desktop)
            
            # 用户下载目录
            user_downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            allowed_dirs.append(user_downloads)
            
            # 检查路径是否在允许的目录范围内
            is_allowed = False
            for allowed_dir in allowed_dirs:
                if os.path.isdir(allowed_dir):
                    if abs_path == allowed_dir or abs_path.startswith(allowed_dir + os.sep):
                        is_allowed = True
                        break
            
            # 如果路径不存在，检查父目录是否在允许范围内（用于创建新文件/目录）
            if not is_allowed:
                parent_dir = os.path.dirname(abs_path)
                if parent_dir:
                    parent_dir = os.path.abspath(parent_dir)
                    for allowed_dir in allowed_dirs:
                        if os.path.isdir(allowed_dir):
                            if parent_dir == allowed_dir or parent_dir.startswith(allowed_dir + os.sep):
                                is_allowed = True
                                break
            
            if not is_allowed:
                print(f"路径不在允许范围内: {abs_path}")
                return False
            
            # 检查符号链接（防止通过符号链接访问受限目录）
            if os.path.islink(abs_path):
                print(f"路径是符号链接，不允许: {abs_path}")
                return False
            
            # 检查父目录是否包含符号链接
            check_path = abs_path
            while check_path != os.path.dirname(check_path):
                check_path = os.path.dirname(check_path)
                if os.path.islink(check_path):
                    print(f"父路径包含符号链接: {check_path}")
                    return False
            
            # 检查路径是否存在且是目录，或者父目录存在（用于创建新目录）
            if os.path.exists(normalized_path):
                if os.path.isdir(normalized_path):
                    return True
                else:
                    print(f"路径不是目录: {normalized_path}")
                    return False
            else:
                # 如果路径不存在，检查父目录是否存在
                parent_dir = os.path.dirname(normalized_path)
                if parent_dir and os.path.exists(parent_dir) and os.path.isdir(parent_dir):
                    return True
            
            return False
        except Exception as e:
            print(f"路径验证错误: {e}")
            return False

    def browse_save_path(self):
        """浏览保存路径"""
        from PyQt5.QtWidgets import QFileDialog
        import os
        
        # 弹出目录选择对话框
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", self.save_directory)
        
        if directory:
            # 验证路径合法性，防止路径注入攻击
            if self.is_valid_path(directory):
                self.save_directory = directory
                self.line_edit_save_path.setText(self.save_directory)
                self.append_text(f"[系统]: 保存路径已设置为: {self.save_directory}\n")
                # 保存配置
                self.save_config()
            else:
                self.append_text("[错误]: 无效的保存路径\n")
                QMessageBox.warning(self, "警告", "无效的保存路径，请选择有效的目录")

    def toggle_auto_save(self, state):
        """切换自动保存功能"""
        if state == 2:  # 勾选
            self.auto_save_enabled = True
            # 创建新的日志文件
            self.create_new_log_file()
            
            # 保存之前接收到的数据到新创建的日志文件
            if self.current_log_file and self.log_buffer:
                try:
                    # 遍历日志缓冲区，将所有数据写入文件
                    for log_entry in self.log_buffer:
                        self.current_log_file.write(log_entry + '\n')
                    self.current_log_file.flush()
                    # 强制写入磁盘（开启时一次性落盘，后续由定时器接管）
                    os.fsync(self.current_log_file.fileno())
                    # 更新文件大小
                    total_size = 0
                    for log_entry in self.log_buffer:
                        total_size += len((log_entry + '\n').encode('utf-8'))
                    self.log_file_size += total_size
                    # 更新文件大小进度条
                    self.append_text("[系统]: 已保存历史数据到日志文件\n")
                except Exception as e:
                    self.append_text(f"[错误]: 保存历史数据失败: {str(e)}\n")
            
            # 启动定时刷盘定时器
            if hasattr(self, '_log_fsync_timer'):
                self._log_fsync_timer.start()
            self.append_text("[系统]: 自动保存功能已开启\n")
        else:  # 取消勾选
            self.auto_save_enabled = False
            # 停止定时刷盘定时器，并做最后一次落盘
            if hasattr(self, '_log_fsync_timer'):
                self._log_fsync_timer.stop()
            self._fsync_log_file()
            # 关闭当前日志文件
            if self.current_log_file:
                try:
                    self.current_log_file.close()
                    self.current_log_file = None
                    self.append_text("[系统]: 日志文件已关闭\n")
                except Exception as e:
                    self.append_text(f"[错误]: 关闭日志文件失败: {str(e)}\n")
            # 更新状态栏日志文件显示
            self.status_log.setText("日志文件: 未创建")
            self.append_text("[系统]: 自动保存功能已关闭\n")
        
        # 保存配置
        self.save_config()


    
    def update_rts_dtr(self):
        """更新RTS和DTR状态（仅串口模式有效）"""
        if getattr(self, 'connection_mode', 'serial') != 'serial':
            return
        if hasattr(self, 'transport') and self.transport and self.transport.is_open:
            try:
                self.transport.rts = self.check_rts.isChecked()
                self.transport.dtr = self.check_dtr.isChecked()
            except Exception as e:
                self.append_text(f"[错误]: 设置RTS/DTR失败: {str(e)}\n")

    def calculate_checksum(self, data, checksum_type):
        """计算校验值"""
        if checksum_type == "None":
            return b""
        
        if isinstance(data, str):
            data = data.encode('utf-8')
        
        if checksum_type == "XOR8":
            # XOR8校验
            checksum = 0
            for byte in data:
                checksum ^= byte
            return bytes([checksum])
        
        elif checksum_type == "ADD8":
            # ADD8校验
            checksum = sum(data) & 0xFF
            return bytes([checksum])
        
        elif checksum_type == "ADD16":
            # ADD16校验
            checksum = sum(data) & 0xFFFF
            return checksum.to_bytes(2, byteorder='big')
        
        elif checksum_type == "ModbusCRC16":
            # Modbus CRC16校验
            crc = 0xFFFF
            for byte in data:
                crc ^= byte
                for _ in range(8):
                    if crc & 0x0001:
                        crc = (crc >> 1) ^ 0xA001
                    else:
                        crc >>= 1
            return crc.to_bytes(2, byteorder='little')
        
        elif checksum_type == "CRC32":
            # CRC32校验
            import zlib
            crc = zlib.crc32(data)
            return crc.to_bytes(4, byteorder='big')
        
        elif checksum_type == "Fletcher":
            # Fletcher校验
            sum1 = 0
            sum2 = 0
            for byte in data:
                sum1 = (sum1 + byte) % 255
                sum2 = (sum2 + sum1) % 255
            return bytes([sum1, sum2])
    
    def on_send_multi_btn_clicked(self):
        """处理多字符发送按钮点击，通过遍历查找sender所在行"""
        sender = self.sender()
        if sender:
            for row in range(self.table_multi_send.rowCount()):
                widget = self.table_multi_send.cellWidget(row, 2)
                if widget and widget.layout() and widget.layout().count() > 0:
                    if widget.layout().itemAt(0).widget() == sender:
                        self.send_multi_item(row)
                        return

    @pyqtSlot(int)
    def send_multi_item(self, row):
        """发送多字符列表中的项目"""
        # 检查串口状态（静默检查，不显示提示框）
        if not hasattr(self, 'transport') or not self.transport or not self.transport.is_open:
            return
        
        # 获取HEX复选框状态
        hex_widget = self.table_multi_send.cellWidget(row, 0)
        if not hex_widget:
            return
        
        hex_layout = hex_widget.layout()
        if not hex_layout:
            return
        
        hex_checkbox = hex_layout.itemAt(0).widget()
        if not hex_checkbox:
            return
        
        is_hex = hex_checkbox.isChecked()
        
        # 获取字符串
        string_item = self.table_multi_send.item(row, 1)
        if not string_item:
            return
        
        data = string_item.text()
        if not data.strip():
            return
        
        # 保存当前的HEX发送设置
        original_hex_send = self.check_hex_send.isChecked()
        
        try:
            # 设置HEX发送状态
            self.check_hex_send.setChecked(is_hex)
            
            # 设置发送文本
            self.text_send.setPlainText(data)
            
            # 发送数据
            self.send_data()
        except Exception as e:
            print(f"发送多字符项目错误: {e}")
        finally:
            # 恢复原始HEX发送设置
            self.check_hex_send.setChecked(original_hex_send)
    
    def add_multi_item(self):
        """添加新的多字符项目"""
        row = self.table_multi_send.rowCount()
        self.table_multi_send.insertRow(row)
        
        # HEX复选框
        hex_checkbox = QCheckBox()
        hex_checkbox.setChecked(False)
        hex_widget = QWidget()
        hex_layout = QHBoxLayout(hex_widget)
        hex_layout.addWidget(hex_checkbox)
        hex_layout.setAlignment(Qt.AlignCenter)
        hex_layout.setContentsMargins(0, 0, 0, 0)
        self.table_multi_send.setCellWidget(row, 0, hex_widget)
        
        # 字符串
        self.table_multi_send.setItem(row, 1, QTableWidgetItem(""))
        
        # 发送按钮（支持三击编辑）
        send_btn = QPushButton("无注释")
        send_btn.setFont(QFont("Microsoft YaHei", 9))
        send_btn.setMinimumWidth(70)  # 增加按钮宽度
        send_btn.clicked.connect(self.on_send_multi_btn_clicked)
        send_btn.setObjectName(f"btn_{row}")
        send_btn.installEventFilter(self)
        send_widget = QWidget()
        send_layout = QHBoxLayout(send_widget)
        send_layout.addWidget(send_btn)
        send_layout.setAlignment(Qt.AlignCenter)
        send_layout.setContentsMargins(0, 0, 0, 0)
        self.table_multi_send.setCellWidget(row, 2, send_widget)
        
        # 延时
        delay_spin = QSpinBox()
        delay_spin.setRange(0, 10000)
        delay_spin.setValue(1000)
        delay_spin.setFont(QFont("Consolas", 9))
        delay_widget = QWidget()
        delay_layout = QHBoxLayout(delay_widget)
        delay_layout.addWidget(delay_spin)
        delay_layout.setAlignment(Qt.AlignCenter)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        self.table_multi_send.setCellWidget(row, 3, delay_widget)
        
        # 顺序显示框
        # 为新添加的项目设置默认顺序值
        default_order = row + 1  # 默认顺序为行号+1
        order_item = QTableWidgetItem(str(default_order))
        order_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        order_item.setTextAlignment(Qt.AlignCenter)  # 文本居中显示
        self.table_multi_send.setItem(row, 4, order_item)
    
    def remove_multi_item(self):
        """删除选中的多字符项目"""
        selected_rows = set()
        for item in self.table_multi_send.selectedItems():
            selected_rows.add(item.row())
        
        # 按降序删除，避免行索引变化
        for row in sorted(selected_rows, reverse=True):
            self.table_multi_send.removeRow(row)
    
    def clear_all_items(self):
        """清空所有多字符项目"""
        reply = QMessageBox.question(self, "确认清空", "确定要清空所有列表内指令吗？", 
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.table_multi_send.setRowCount(0)
            self.append_text("[系统]: 已清空所有列表内指令\n")
    
    def toggle_multi_send(self):
        """切换多字符发送区域的显示/隐藏状态"""
        right_content = self.main_splitter.widget(1)
        if right_content.isVisible():
            right_content.hide()
            self.btn_toggle_multi_send.setText("显示多字符发送")
            if hasattr(self, 'act_multi_send'):
                self.act_multi_send.setChecked(False)
            # 调整左侧大小
            self.main_splitter.setSizes([1000, 0])
        else:
            right_content.show()
            self.btn_toggle_multi_send.setText("隐藏多字符发送")
            if hasattr(self, 'act_multi_send'):
                self.act_multi_send.setChecked(True)
            # 确保所有相关组件的大小策略正确
            right_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.multi_send_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.table_multi_send.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            
            # 确保分割器设置正确
            self.main_splitter.setHandleWidth(8)
            self.main_splitter.setOpaqueResize(True)
            self.main_splitter.setStretchFactor(0, 1)
            self.main_splitter.setStretchFactor(1, 1)
            
            # 恢复分割器大小
            self.main_splitter.setSizes([900, 100])
            
            # 强制刷新布局
            self.main_splitter.update()
            self.main_splitter.repaint()
    
    def toggle_cycle_count(self, state):
        """切换循环次数输入框的启用状态"""
        self.spin_cycle_count.setEnabled(state == 2)
    
    def show_multi_send_help(self):
        """显示多字符发送使用教程"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QTextEdit
        
        dialog = QDialog(self)
        dialog.setWindowTitle("多字符发送使用教程")
        dialog.setMinimumSize(500, 400)
        
        layout = QVBoxLayout(dialog)
        
        # 标题
        title_label = QLabel("多字符发送使用教程")
        title_label.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        layout.addWidget(title_label)
        
        # 内容
        help_text = """
        多字符发送功能使用说明：
        
        1. 循环发送：
           - 勾选"循环发送"复选框，系统将按照顺序循环发送所有有效项目
           - 可设置发送间隔时间（毫秒）
           - 再次点击复选框可停止循环发送
           - 循环发送时，次数相关控件将被禁用，无法修改
        
        2. 循环次数：
           - 勾选"次数:"复选框，可设置循环发送的轮数
           - 在输入框中设置具体的循环次数（1-9999）
           - 达到指定次数后，循环发送会自动停止
        
        3. 字符串：
           - 在"字符串"列输入要发送的内容
           - 支持普通文本和HEX格式（需要勾选HEX复选框）
        
        4. 单击发送：
           - 点击"点击发送"列的按钮，可单独发送对应行的内容
           - 按钮文本可自定义
        
        5. 修改内容：
           - 双击"字符串"列可编辑发送内容
           - 右键点击"发送"按钮可重命名按钮文字
        
        6. 延时设置：
           - 在"延时(ms)"列设置每次发送后的等待时间
        
        7. 顺序设置：
           - 在"顺序"列设置发送的顺序
           - 顺序值必须大于0
           - 系统会按照顺序值从小到大发送
        
        8. 保存/加载：
           - 点击"保存"按钮可将当前配置保存到文件
           - 点击"加载"按钮可从文件加载配置
        
        9. 添加/删除/清空：
           - 点击"+"按钮添加新行
           - 选中行后点击"-"按钮删除行
           - 点击"清空"按钮可清空所有列表内指令
        
        10. 注意事项：
           - 只有顺序值大于0的项目才会被发送
           - 批量发送时，每个项目使用各自的延时设置
           - 循环完成后，使用顶部的延时设置作为下一次循环的间隔
        11. 关于作者：
           - 设计者:gaoxiang
           - 联系方式:770807059@qq.com
        """
        
        help_text_edit = QTextEdit()
        help_text_edit.setText(help_text)
        help_text_edit.setReadOnly(True)
        help_text_edit.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(help_text_edit)
        
        # 关闭按钮
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        self._apply_dialog_theme(dialog)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    @pyqtSlot()
    def stop_batch_send(self):
        """停止批量发送（从线程调用）"""
        # 使用互斥锁保护
        with QMutexLocker(self.serial_mutex):
            if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
                self.batch_thread.stop()
                # 使用QTimer延迟设置，避免信号循环
                QTimer.singleShot(0, lambda: self.check_cycle_send.setChecked(False))
                self.append_text("[系统]: 串口已关闭，批量发送已停止\n")
                # 延迟清理线程
                QTimer.singleShot(100, self._cleanup_batch_thread)
    
    def toggle_batch_send(self, state):
        """切换批量发送状态"""
        if state == 2:  # 勾选，开始发送
            should_start = False
            
            # 使用互斥锁保护对共享资源的访问
            with QMutexLocker(self.serial_mutex):
                # 检查是否已经有批量发送线程在运行
                if hasattr(self, 'batch_thread'):
                    if self.batch_thread.isRunning():
                        # 线程正在运行，不能启动新线程
                        should_start = False
                    else:
                        # 线程已停止，删除旧线程引用
                        del self.batch_thread
                        should_start = True
                else:
                    # 没有线程，允许启动
                    should_start = True
            
            # 禁用次数相关控件（在锁外执行，避免死锁）
            self.check_cycle_count.setEnabled(False)
            self.spin_cycle_count.setEnabled(False)
            
            # 如果不应该启动，恢复UI状态并返回
            if not should_start:
                QTimer.singleShot(0, lambda: self.check_cycle_send.setChecked(False))
                self.check_cycle_count.setEnabled(True)
                self.spin_cycle_count.setEnabled(self.check_cycle_count.isChecked())
                return
            
            # 检查串口是否打开（在锁外执行，避免死锁）
            if not hasattr(self, 'transport') or not self.transport or not self.transport.is_open:
                QMessageBox.warning(self, "警告", "请先打开连接！")
                # 使用QTimer延迟设置，避免信号循环
                QTimer.singleShot(0, lambda: self.check_cycle_send.setChecked(False))
                # 重新启用次数相关控件
                QTimer.singleShot(0, lambda: self.check_cycle_count.setEnabled(True))
                QTimer.singleShot(0, lambda: self.spin_cycle_count.setEnabled(self.check_cycle_count.isChecked()))
                return
            
            # 启动批量发送
            self.batch_send()
        else:  # 取消勾选，停止发送
            # 停止批量发送（在锁外检查状态，避免死锁）
            thread_stopped = False
            with QMutexLocker(self.serial_mutex):
                if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
                    self.batch_thread.stop()
                    thread_stopped = True
            
            if thread_stopped:
                # 使用QTimer延迟处理，确保线程有时间停止
                QTimer.singleShot(100, lambda: self.append_text("[系统]: 批量发送已停止\n"))
                QTimer.singleShot(100, self._cleanup_batch_thread)
            else:
                # 如果线程没有运行，直接清理
                self._cleanup_batch_thread()
            
            # 重新启用次数相关控件
            self.check_cycle_count.setEnabled(True)
            self.spin_cycle_count.setEnabled(self.check_cycle_count.isChecked())
    
    def _cleanup_batch_thread(self):
        """清理批量发送线程"""
        # 使用互斥锁保护
        with QMutexLocker(self.serial_mutex):
            # 清除batch_thread属性
            if hasattr(self, 'batch_thread'):
                # 检查线程是否还在运行
                if self.batch_thread.isRunning():
                    # 线程还在运行，不删除属性
                    return
                # 线程已停止，删除属性
                del self.batch_thread
    
    def batch_send(self):
        """批量发送多字符项目"""
        # 使用互斥锁保护
        with QMutexLocker(self.serial_mutex):
            count = self.table_multi_send.rowCount()
            if count == 0:
                QMessageBox.information(self, "提示", "没有可发送的项目")
                self.check_cycle_send.setChecked(False)
                return
            
            # 获取全局循环延时设置
            cycle_delay = self.spin_delay.value()
            
            # 获取循环次数设置
            use_cycle_count = self.check_cycle_count.isChecked()
            cycle_count = self.spin_cycle_count.value() if use_cycle_count else -1  # -1 表示无限循环
            
            # 收集所有有效的项目（顺序不为0）
            valid_items = []
            for i in range(count):
                # 获取顺序值
                order_item = self.table_multi_send.item(i, 4)
                order_text = order_item.text().strip() if order_item else ""
                
                # 跳过空顺序和0顺序的项目
                if not order_text:
                    continue
                
                try:
                    order = int(order_text)
                    if order <= 0:
                        continue
                    
                    # 获取当前项目的延时设置
                    delay_widget = self.table_multi_send.cellWidget(i, 3)
                    if delay_widget and delay_widget.layout():
                        delay_layout = delay_widget.layout()
                        if delay_layout:
                            delay_spin = delay_layout.itemAt(0).widget()
                            item_delay = delay_spin.value() if delay_spin else 1000
                        else:
                            item_delay = 1000
                    else:
                        item_delay = 1000
                    
                    valid_items.append((order, i, item_delay))
                except ValueError:
                    continue
            
            # 按顺序排序
            valid_items.sort(key=lambda x: x[0])
            sorted_items = [(index, delay) for _, index, delay in valid_items]
            
            if not sorted_items:
                QMessageBox.information(self, "提示", "没有有效的发送项目（顺序必须大于0）")
                self.check_cycle_send.setChecked(False)
                return
        
        # 开始批量发送（在锁外执行，避免死锁）
        if use_cycle_count:
            self.append_text(f"[系统]: 开始批量发送 {len(sorted_items)} 个项目，共 {cycle_count} 轮\n")
        else:
            self.append_text(f"[系统]: 开始批量发送 {len(sorted_items)} 个项目\n")
        
        # 创建一个线程来处理批量发送
        class BatchSendThread(QThread):
            def __init__(self, parent, sorted_items, cycle_delay, use_cycle_count, cycle_count):
                super().__init__(parent)
                self.parent = parent
                self.sorted_items = sorted_items  # 格式: [(index, delay), ...]
                self.cycle_delay = cycle_delay  # 循环完成后的延时
                self.use_cycle_count = use_cycle_count  # 是否使用循环次数
                self.cycle_count = cycle_count  # 循环次数
                self.running = True
            
            def run(self):
                i = 0
                current_cycle = 0
                
                while self.running:
                    try:
                        # 检查父对象是否仍然存在
                        if not self.parent:
                            self.running = False
                            break
                        
                        # 使用互斥锁保护串口访问
                        with QMutexLocker(self.parent.serial_mutex):
                            # 检查串口状态
                            if not hasattr(self.parent, 'transport') or not self.parent.transport or not self.parent.transport.is_open:
                                self.running = False
                                break
                        
                        # 获取当前项目的索引和延时
                        current_index, current_delay = self.sorted_items[i]
                        
                        # 发送当前项目（在主线程中执行）
                        # 发送操作本身会检查串口状态
                        QMetaObject.invokeMethod(self.parent, "send_multi_item", Qt.QueuedConnection,
                                                Q_ARG(int, current_index))
                        
                        # 添加延时（使用当前项目的延时设置）
                        if current_delay > 0:
                            QThread.msleep(current_delay)
                        else:
                            # 即使没有延时，也添加一个小的休眠，避免CPU占用过高
                            QThread.msleep(10)
                        
                        # 循环发送，发送完最后一个项目后重新开始
                        i = (i + 1) % len(self.sorted_items)
                        
                        # 如果刚发送完最后一个项目，添加循环延时
                        if i == 0:
                            # 增加循环计数
                            current_cycle += 1
                            
                            # 检查是否达到循环次数
                            if self.use_cycle_count and current_cycle >= self.cycle_count:
                                self.running = False
                                break
                            
                            # 添加循环延时
                            if self.cycle_delay > 0:
                                QThread.msleep(self.cycle_delay)
                    except serial.SerialException as e:
                        # 捕获串口异常
                        print(f"批量发送串口错误: {e}")
                        self.running = False
                        try:
                            QMetaObject.invokeMethod(self.parent, "stop_batch_send", Qt.QueuedConnection)
                        except:
                            pass
                        break
                    except RuntimeError as e:
                        # 捕获运行时错误
                        print(f"批量发送运行时错误: {e}")
                        self.running = False
                        break
                    except Exception as e:
                        # 捕获其他未预期的异常，记录详细信息便于调试
                        import traceback
                        error_details = traceback.format_exc()
                        print(f"批量发送线程错误: {e}")
                        print(f"错误详情: {error_details}")
                        self.running = False
                        break
            
            def stop(self):
                """停止线程，设置超时避免无限等待"""
                self.running = False
                # 使用带超时的wait()，避免线程阻塞
                if self.isRunning():
                    if not self.wait(2000):  # 2秒超时
                        print(f"警告: 批量发送线程停止超时")
        
        # 启动批量发送线程（在锁外执行，避免死锁）
        self.batch_thread = BatchSendThread(self, sorted_items, cycle_delay, use_cycle_count, cycle_count)
        self.batch_thread.finished.connect(lambda: self.append_text("[系统]: 批量发送完成\n"))
        self.batch_thread.start()
    

    
    def save_multi_items(self):
        """保存多字符项目到CSV文件"""
        try:
            import csv
            file_path, _ = QFileDialog.getSaveFileName(
                self, "保存多字符项目", "", "CSV Files (*.csv)"
            )
            if not file_path:
                return

            # 确保文件扩展名为.csv
            if not file_path.endswith('.csv'):
                file_path += '.csv'

            # 收集所有项目
            items = []
            for i in range(self.table_multi_send.rowCount()):
                # 获取HEX状态
                hex_widget = self.table_multi_send.cellWidget(i, 0)
                if hex_widget and hex_widget.layout():
                    hex_layout = hex_widget.layout()
                    hex_checkbox = hex_layout.itemAt(0).widget()
                    is_hex = hex_checkbox.isChecked() if hex_checkbox else False
                else:
                    is_hex = False

                # 获取字符串
                string_item = self.table_multi_send.item(i, 1)
                text = string_item.text() if string_item else ""

                # 获取延时
                delay_widget = self.table_multi_send.cellWidget(i, 3)
                if delay_widget and delay_widget.layout():
                    delay_layout = delay_widget.layout()
                    delay_spin = delay_layout.itemAt(0).widget()
                    delay = delay_spin.value() if delay_spin else 1000
                else:
                    delay = 1000

                # 获取按钮文本
                button_widget = self.table_multi_send.cellWidget(i, 2)
                button_text = "无注释"
                if button_widget and button_widget.layout():
                    button_layout = button_widget.layout()
                    if button_layout and button_layout.count() > 0:
                        btn = button_layout.itemAt(0).widget()
                        if btn:
                            button_text = btn.text()

                # 获取顺序
                order_item = self.table_multi_send.item(i, 4)
                order = order_item.text().strip() if order_item else "1"

                items.append({"hex": is_hex, "string": text, "button_text": button_text, "delay": delay, "order": order})

            # 保存到CSV文件
            with open(file_path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['hex', 'string', 'button_text', 'delay', 'order'])
                writer.writeheader()
                for item in items:
                    writer.writerow(item)
            
            self.append_text(f"[系统]: 多字符项目已保存到 {file_path}\n")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存多字符项目失败: {e}")
            self.append_text(f"[错误]: 保存多字符项目失败: {e}\n")

    def eventFilter(self, obj, event):
        """事件过滤器：右键重命名按钮文本 / 发送栏上下键历史记录导航 / 接收区滚轮暂停跟底"""
        # 构造期间可能被提前调用，对尚未创建的控件做防御性判断
        text_recv = getattr(self, 'text_recv', None)
        text_send = getattr(self, 'text_send', None)

        # ---- 接收区滚轮：用户滚动查看时暂停自动跟底，6 秒静止后恢复 ----
        if text_recv is not None and obj is text_recv.viewport() and event.type() == QEvent.Wheel:
            self._on_recv_wheel(event)

        # ---- 右键重命名多字符发送按钮 ----
        if event.type() == event.MouseButtonPress and event.button() == Qt.RightButton:
            table = getattr(self, 'table_multi_send', None)
            if table is not None and obj.objectName().startswith("btn_"):
                for row in range(table.rowCount()):
                    w = table.cellWidget(row, 2)
                    if w and w.layout() and w.layout().itemAt(0).widget() == obj:
                        new_text, ok = QInputDialog.getText(
                            self, "重命名按钮", "按钮文字:", text=obj.text())
                        if ok and new_text:
                            obj.setText(new_text)
                        break
                return True  # 消费右键事件，不继续传播

        # ---- 发送栏上下键历史记录导航 ----
        if text_send is not None and obj is text_send:
            if event.type() == event.KeyPress:
                key = event.key()
                if key == Qt.Key_Up:
                    self._navigate_history(-1)  # 向上 = 更早的历史
                    return True
                elif key == Qt.Key_Down:
                    self._navigate_history(1)   # 向下 = 更新的历史
                    return True

        return super().eventFilter(obj, event)

    def _navigate_history(self, direction):
        """在发送历史记录中导航。
        direction: -1 = 上键（更早的记录），1 = 下键（更新的记录）
        """
        total = len(self.send_history)
        if total == 0:
            return

        if direction == -1:  # 上键 → 更早的记录
            if self.history_index == -1:
                # 首次进入历史：保存当前文本作为草稿
                self._draft_text = self.text_send.toPlainText()
                self.history_index = 0
            elif self.history_index < total - 1:
                self.history_index += 1
            else:
                return  # 已到最早记录，不再变化
        elif direction == 1:  # 下键 → 更新的记录
            if self.history_index == -1:
                return  # 未在历史模式，下键无操作
            elif self.history_index > 0:
                self.history_index -= 1
            else:
                # history_index == 0，退出历史模式，恢复草稿
                self.history_index = -1
                self._setting_history_text = True
                self.text_send.setPlainText(self._draft_text)
                self._setting_history_text = False
                # 将光标移到末尾
                cursor = self.text_send.textCursor()
                cursor.movePosition(cursor.End)
                self.text_send.setTextCursor(cursor)
                return
        else:
            return

        # 设置历史文本
        history_item = self.send_history[self.history_index]
        self._setting_history_text = True
        self.text_send.setPlainText(history_item)
        self._setting_history_text = False
        # 将光标移到末尾
        cursor = self.text_send.textCursor()
        cursor.movePosition(cursor.End)
        self.text_send.setTextCursor(cursor)

    def _on_send_text_changed(self):
        """发送栏文本变化回调：用户手动编辑时退出历史导航模式"""
        if not self._setting_history_text and self.history_index != -1:
            self.history_index = -1




    def _try_decode_csv(self, file_path):
        """尝试用多种编码读取CSV文件，返回(文本内容, 使用的编码)或(None, None)"""
        import csv
        import io

        # 先读取原始字节
        try:
            with open(file_path, 'rb') as f:
                raw_bytes = f.read()
        except Exception:
            return None, None

        if len(raw_bytes) == 0:
            return None, None

        # BOM 检测：UTF-8-BOM 以 EF BB BF 开头
        has_utf8_bom = raw_bytes[:3] == b'\xef\xbb\xbf'
        # UTF-16 LE BOM: FF FE, UTF-16 BE BOM: FE FF
        has_utf16_bom = raw_bytes[:2] in (b'\xff\xfe', b'\xfe\xff')

        # 按优先级尝试编码
        encodings_to_try = ['utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'gb2312', 'latin-1']
        # 如果检测到 GBK/GB18030 特征字节（首字节在 0x81-0xFE 范围），优先尝试中文编码
        if not has_utf8_bom and not has_utf16_bom:
            first_byte = raw_bytes[0] if raw_bytes else 0
            if 0x81 <= first_byte <= 0xFE:
                encodings_to_try = ['gb18030', 'gbk', 'utf-8-sig', 'utf-8', 'gb2312', 'latin-1']

        for encoding in encodings_to_try:
            try:
                text = raw_bytes.decode(encoding)
                # 验证是否为有效CSV：检查是否包含预期的列头
                test_io = io.StringIO(text)
                reader = csv.DictReader(test_io)
                if reader.fieldnames and 'string' in reader.fieldnames:
                    return text, encoding
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception:
                continue

        # 所有编码都失败，尝试仅用 chardet（如果可用）作为最后手段
        try:
            import chardet
            result = chardet.detect(raw_bytes)
            if result and result['encoding'] and result['confidence'] > 0.5:
                encoding = result['encoding']
                try:
                    text = raw_bytes.decode(encoding)
                    test_io = io.StringIO(text)
                    reader = csv.DictReader(test_io)
                    if reader.fieldnames and 'string' in reader.fieldnames:
                        return text, encoding
                except Exception:
                    pass
        except ImportError:
            pass

        return None, None

    def load_multi_items(self):
        """从CSV文件加载多字符项目"""
        try:
            import csv
            import io
            file_path, _ = QFileDialog.getOpenFileName(
                self, "加载多字符项目", "", "CSV Files (*.csv)"
            )
            if not file_path:
                return

            text_content, _ = self._try_decode_csv(file_path)
            if text_content is None:
                QMessageBox.critical(
                    self, "加载失败",
                    "无法读取CSV文件，文件可能已被系统安全策略加密或使用了不支持的编码。\n\n"
                    "建议：\n"
                    "1. 右键文件 → 属性 → 高级 → 检查'加密内容以便保护数据'是否勾选\n"
                    "2. 若文件被加密，请解密后重新加载\n"
                    "3. 或使用之前保存的未加密备份"
                )
                return

            items = []
            reader = csv.DictReader(io.StringIO(text_content))
            for row in reader:
                try:
                    item = {
                        "hex": row['hex'].lower() == 'true',
                        "string": row.get('string', ''),
                        "button_text": row.get('button_text', '无注释'),
                        "delay": int(row.get('delay', 1000)),
                        "order": row.get('order', '1')
                    }
                    items.append(item)
                except (ValueError, KeyError) as e:
                    continue

            if not items:
                QMessageBox.information(self, "提示", "文件中没有有效的项目")
                return

            # 清空现有项目
            self.table_multi_send.setRowCount(0)

            # 添加加载的项目
            for i, item in enumerate(items):
                self.table_multi_send.insertRow(i)

                # HEX复选框
                hex_checkbox = QCheckBox()
                hex_checkbox.setChecked(item.get("hex", False))
                hex_widget = QWidget()
                hex_layout = QHBoxLayout(hex_widget)
                hex_layout.addWidget(hex_checkbox)
                hex_layout.setAlignment(Qt.AlignCenter)
                hex_layout.setContentsMargins(0, 0, 0, 0)
                self.table_multi_send.setCellWidget(i, 0, hex_widget)

                # 字符串
                string_value = item.get("string", "")
                self.table_multi_send.setItem(i, 1, QTableWidgetItem(string_value))

                # 发送按钮（支持三击编辑，恢复保存的按钮文本）
                button_text = item.get("button_text", "无注释")
                send_btn = QPushButton(button_text)
                send_btn.setFont(QFont("Microsoft YaHei", 9))
                send_btn.setMinimumWidth(70)
                send_btn.clicked.connect(self.on_send_multi_btn_clicked)
                send_btn.setObjectName(f"btn_{i}")
                send_btn.installEventFilter(self)
                send_widget = QWidget()
                send_layout = QHBoxLayout(send_widget)
                send_layout.addWidget(send_btn)
                send_layout.setAlignment(Qt.AlignCenter)
                send_layout.setContentsMargins(0, 0, 0, 0)
                self.table_multi_send.setCellWidget(i, 2, send_widget)

                # 延时
                delay_spin = QSpinBox()
                delay_spin.setRange(0, 10000)
                delay_spin.setValue(item.get("delay", 1000))
                delay_spin.setFont(QFont("Consolas", 9))
                delay_widget = QWidget()
                delay_layout = QHBoxLayout(delay_widget)
                delay_layout.addWidget(delay_spin)
                delay_layout.setAlignment(Qt.AlignCenter)
                delay_layout.setContentsMargins(0, 0, 0, 0)
                self.table_multi_send.setCellWidget(i, 3, delay_widget)

                # 顺序
                order_item = QTableWidgetItem(item.get("order", "1"))
                order_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                order_item.setTextAlignment(Qt.AlignCenter)
                self.table_multi_send.setItem(i, 4, order_item)

            self.append_text(f"[系统]: 从 {file_path} 加载了 {len(items)} 个多字符项目\n")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载多字符项目失败: {e}")
            self.append_text(f"[错误]: 加载多字符项目失败: {e}\n")

    def _toggle_timestamp(self, checked):
        """切换时间戳显示（菜单触发，同步到复选框）"""
        self.check_timestamp.blockSignals(True)
        self.check_timestamp.setChecked(checked)
        self.check_timestamp.blockSignals(False)

    def save_log_manually(self):
        """手动保存接收日志（Ctrl+S）"""
        if not self.log_buffer:
            QMessageBox.information(self, "提示", "接收区暂无数据可保存。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存接收日志", self.save_directory,
            "日志文件 (*.log *.txt);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(self.text_recv.toPlainText())
            self.append_text(f"[系统]: 日志已保存至 {file_path}\n")
            self._set_status(f"日志已保存: {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存文件: {e}")

    def export_data(self):
        """导出接收区数据为文件"""
        text = self.text_recv.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "提示", "接收区暂无数据可导出。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出接收数据", self.save_directory,
            "文本文件 (*.txt);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            encoding = 'utf-8-sig' if file_path.endswith('.csv') else 'utf-8'
            with open(file_path, 'w', encoding=encoding) as f:
                f.write(text)
            self.append_text(f"[系统]: 数据已导出至 {file_path}\n")
            self._set_status(f"导出成功: {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"无法导出数据: {e}")

    def crc_calculator(self):
        """CRC 计算器弹窗"""
        show_crc_calculator(self, is_dark=(self.current_theme == 'dark'))

    def hex_converter(self):
        """HEX 转换器弹窗：HEX ↔ ASCII ↔ Decimal 互转"""
        show_hex_converter(self, is_dark=(self.current_theme == 'dark'))

    def serial_monitor(self):
        """串口监视器：列出系统所有串口及详细信息"""
        show_serial_monitor(self, is_dark=(self.current_theme == 'dark'))

    def oscilloscope(self):
        """数据波形示波器：将串口接收的原始字节按数据类型解析为波形图"""
        try:
            import pyqtgraph as pg
        except ImportError:
            QMessageBox.warning(self, "缺少依赖",
                "示波器功能需要 pyqtgraph 和 numpy 库。\n\n"
                "请在终端执行以下命令安装：\n"
                "  pip install pyqtgraph numpy")
            return

        if not hasattr(self, 'transport') or not self.transport or not self.transport.is_open:
            QMessageBox.warning(self, "提示", "请先打开连接再使用示波器。")
            return

        # ── 数据类型定义 ──
        DATA_TYPES = {
            'uint8':    (1, 'B'),   # (字节数, struct格式)
            'int8':     (1, 'b'),
            'uint16_be':(2, '>H'),
            'int16_be': (2, '>h'),
            'uint32_be':(4, '>I'),
            'float32':  (4, '>f'),
        }

        # ── 对话框 ──
        dialog = QDialog(self)
        dialog.setWindowTitle("数据波形（示波器）")
        dialog.setMinimumSize(900, 550)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        main_layout = QHBoxLayout(dialog)
        main_layout.setSpacing(8)

        # ── 左侧控制面板 ──
        panel = QWidget()
        panel.setMinimumWidth(160)
        panel.setMaximumWidth(200)  # 限制最大宽度，避免面板过宽挤压图表
        panel_layout = QVBoxLayout(panel)
        panel_layout.setSpacing(6)

        lbl = QLabel("通道数")
        lbl.setFont(QFont("Microsoft YaHei", 9))
        panel_layout.addWidget(lbl)
        spin_channels = QSpinBox()
        spin_channels.setRange(1, 8)
        spin_channels.setValue(1)
        spin_channels.setFont(QFont("Consolas", 9))
        panel_layout.addWidget(spin_channels)

        lbl2 = QLabel("数据类型")
        lbl2.setFont(QFont("Microsoft YaHei", 9))
        panel_layout.addWidget(lbl2)
        combo_dtype = QComboBox()
        combo_dtype.addItems(list(DATA_TYPES.keys()))
        combo_dtype.setCurrentText('uint8')
        combo_dtype.setFont(QFont("Consolas", 9))
        panel_layout.addWidget(combo_dtype)

        lbl3 = QLabel("显示点数")
        lbl3.setFont(QFont("Microsoft YaHei", 9))
        panel_layout.addWidget(lbl3)
        spin_points = QSpinBox()
        spin_points.setRange(50, 5000)
        spin_points.setValue(500)
        spin_points.setSingleStep(100)
        spin_points.setFont(QFont("Consolas", 9))
        panel_layout.addWidget(spin_points)

        panel_layout.addSpacing(8)

        # 采样间隔
        lbl4 = QLabel("刷新间隔(ms)")
        lbl4.setFont(QFont("Microsoft YaHei", 9))
        panel_layout.addWidget(lbl4)
        spin_refresh = QSpinBox()
        spin_refresh.setRange(20, 1000)
        spin_refresh.setValue(50)
        spin_refresh.setFont(QFont("Consolas", 9))
        panel_layout.addWidget(spin_refresh)

        panel_layout.addSpacing(8)

        # 控制按钮
        self.scope_running = False

        def on_start():
            self.scope_running = True
            btn_start.setEnabled(False)
            btn_pause.setEnabled(True)

        def on_pause():
            self.scope_running = False
            btn_start.setEnabled(True)
            btn_pause.setEnabled(False)

        btn_start = QPushButton("开始")
        btn_start.setFont(QFont("Microsoft YaHei", 9))
        btn_start.clicked.connect(on_start)
        panel_layout.addWidget(btn_start)

        btn_pause = QPushButton("暂停")
        btn_pause.setFont(QFont("Microsoft YaHei", 9))
        btn_pause.setEnabled(False)
        btn_pause.clicked.connect(on_pause)
        panel_layout.addWidget(btn_pause)

        btn_clear = QPushButton("清除")
        btn_clear.setFont(QFont("Microsoft YaHei", 9))
        panel_layout.addWidget(btn_clear)

        panel_layout.addStretch()

        # 当前值标签
        self.scope_value_label = QLabel("当前值: —")
        self.scope_value_label.setFont(QFont("Consolas", 9))
        self.scope_value_label.setWordWrap(True)
        panel_layout.addWidget(self.scope_value_label)

        main_layout.addWidget(panel)

        # ── 右侧波形图 ──
        plot_bg = '#2C313C' if self.current_theme == 'dark' else '#FFFFFF'
        plot_widget = pg.PlotWidget()
        plot_widget.setBackground(plot_bg)
        plot_widget.showGrid(x=True, y=True, alpha=0.3)
        plot_widget.setLabel('left', '数值')
        plot_widget.setLabel('bottom', '采样序号')
        plot_widget.addLegend()
        # 空状态提示文字
        empty_color = (0xAB, 0xB2, 0xBF) if self.current_theme == 'dark' else (0x66, 0x66, 0x66)
        empty_text = pg.TextItem('等待串口数据…', color=empty_color, anchor=(0.5, 0.5))
        empty_text.setFont(QFont("Microsoft YaHei", 10))
        plot_widget.addItem(empty_text)
        main_layout.addWidget(plot_widget, stretch=1)

        # ── 每通道曲线 + 环形缓冲区 ──
        CHANNEL_COLORS = [
            (0x61, 0xAF, 0xEF), (0x98, 0xC3, 0x79), (0xE0, 0x6C, 0x75),
            (0xD1, 0x9A, 0x66), (0xC6, 0x78, 0xDD), (0x56, 0xB6, 0xC2),
            (0xAB, 0xB2, 0xBF), (0xE5, 0xC0, 0x7B),
        ]
        curves = []
        buffers = []

        def rebuild_channels():
            """重建通道曲线和缓冲区"""
            nonlocal curves, buffers
            curves.clear()
            buffers.clear()
            plot_widget.clear()
            n = spin_channels.value()
            for i in range(n):
                r, g, b = CHANNEL_COLORS[i % len(CHANNEL_COLORS)]
                pen = pg.mkPen(color=(r, g, b), width=1.5)
                curve = plot_widget.plot([], [], pen=pen, name=f'CH{i+1}')
                curves.append(curve)
                buffers.append(deque(maxlen=spin_points.value()))
            spin_points.valueChanged.connect(lambda v: [b.__setattr__('maxlen', v) for b in buffers])

        rebuild_channels()
        spin_channels.valueChanged.connect(lambda: rebuild_channels())

        def on_clear():
            for b in buffers:
                b.clear()
            for c in curves:
                c.setData([], [])
            empty_text.setVisible(True)
            self.scope_value_label.setText('当前值: —')

        btn_clear.clicked.connect(on_clear)

        # ── 数据缓冲区与解析 ──
        byte_buffer = bytearray()  # 未解析完的残留字节

        def feed_data(data: bytes):
            """串口数据回调：累积字节并按数据类型+通道数解析为数值"""
            if not self.scope_running:
                return
            nonlocal byte_buffer
            byte_buffer.extend(data)
            # 防止数据格式不匹配导致无限增长（上限 1 MiB）
            if len(byte_buffer) > 1024 * 1024:
                del byte_buffer[:len(byte_buffer) - 512 * 1024]

            dtype_key = combo_dtype.currentText()
            elem_size, fmt = DATA_TYPES[dtype_key]
            num_channels = spin_channels.value()
            frame_size = elem_size * num_channels

            # 尽可能解析完整帧
            while len(byte_buffer) >= frame_size:
                frame = bytes(byte_buffer[:frame_size])
                del byte_buffer[:frame_size]

                for ch in range(num_channels):
                    chunk = frame[ch * elem_size : (ch + 1) * elem_size]
                    try:
                        val = struct.unpack(fmt, chunk)[0]
                    except struct.error:
                        val = 0
                    buffers[ch].append(val)

        # ── 定时刷新波形 ──
        def update_plot():
            if not self.scope_running:
                return
            if not buffers:
                return
            has_data = any(len(b) > 0 for b in buffers)
            if has_data:
                empty_text.setVisible(False)
                for i, curve in enumerate(curves):
                    if buffers[i]:
                        curve.setData(list(buffers[i]))
                # 更新当前值标签
                vals = []
                for i, buf in enumerate(buffers):
                    if buf:
                        vals.append(f'CH{i+1}: {buf[-1]}')
                self.scope_value_label.setText('\n'.join(vals) if vals else '当前值: —')
            else:
                self.scope_value_label.setText('当前值: —')

        refresh_timer = QTimer()
        refresh_timer.timeout.connect(update_plot)
        spin_refresh.valueChanged.connect(lambda v: refresh_timer.setInterval(v))
        refresh_timer.setInterval(50)

        # ── 挂接串口数据 ──
        if hasattr(self, 'read_thread') and self.read_thread:
            self.read_thread.receive_data_signal.connect(feed_data)

        # ── 对话框生命周期 ──
        on_start()
        refresh_timer.start()

        self._apply_dialog_theme(dialog)
        dialog.setAttribute(Qt.WA_DeleteOnClose)

        def on_close():
            refresh_timer.stop()
            self.scope_running = False
            if hasattr(self, 'read_thread') and self.read_thread:
                try:
                    self.read_thread.receive_data_signal.disconnect(feed_data)
                except (TypeError, RuntimeError):
                    pass
        dialog.finished.connect(on_close)

        dialog.show()

    def modbus_tool(self):
        """Modbus 工具：帧构建 + 帧解析"""
        def modbus_crc16(data: bytes) -> int:
            crc = 0xFFFF
            for b in data:
                crc ^= b
                for _ in range(8):
                    if crc & 1: crc = (crc >> 1) ^ 0xA001
                    else:       crc >>= 1
            return crc

        FUNC_CODES = {
            '01 (0x01) 读线圈':        (1,  'read_bits'),
            '02 (0x02) 读离散输入':    (2,  'read_bits'),
            '03 (0x03) 读保持寄存器':  (3,  'read_regs'),
            '04 (0x04) 读输入寄存器':  (4,  'read_regs'),
            '05 (0x05) 写单线圈':      (5,  'write_single'),
            '06 (0x06) 写单寄存器':    (6,  'write_single'),
            '15 (0x0F) 写多线圈':      (15, 'write_multi'),
            '16 (0x10) 写多寄存器':    (16, 'write_multi'),
        }
        EXCEPTIONS = {1: '非法功能码', 2: '非法数据地址', 3: '非法数据值',
                      4: '从站设备故障', 5: '确认', 6: '从站设备忙'}

        dialog = QDialog(self)
        dialog.setWindowTitle("Modbus 工具")
        dialog.setMinimumSize(580, 380)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        main_layout = QVBoxLayout(dialog)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)
        tabs = QTabWidget()
        main_layout.addWidget(tabs)

        # ═══════════════════════════════════════
        #  Tab 1: 帧构建
        # ═══════════════════════════════════════
        build_tab = QWidget()
        bl = QVBoxLayout(build_tab)
        bl.setContentsMargins(8, 4, 8, 4)
        bl.setSpacing(4)

        # ── 协议选择（互斥）──
        proto_row = QHBoxLayout()
        proto_row.setContentsMargins(0, 0, 0, 0)
        proto_row.addWidget(QLabel("协议:"))
        radio_rtu = QRadioButton("RTU")
        radio_rtu.setChecked(True)
        proto_row.addWidget(radio_rtu)
        radio_ascii = QRadioButton("ASCII")
        proto_row.addWidget(radio_ascii)
        radio_tcp = QRadioButton("TCP")
        proto_row.addWidget(radio_tcp)
        proto_group = QButtonGroup(dialog)
        proto_group.addButton(radio_rtu, 0)
        proto_group.addButton(radio_ascii, 1)
        proto_group.addButton(radio_tcp, 2)
        proto_row.addSpacing(16)
        lbl_tcp_unit = QLabel("单元ID:")
        proto_row.addWidget(lbl_tcp_unit)
        spin_tcp_unit = QSpinBox()
        spin_tcp_unit.setRange(0, 255)
        spin_tcp_unit.setValue(1)
        spin_tcp_unit.setFont(QFont("Consolas", 9))
        proto_row.addWidget(spin_tcp_unit)
        proto_row.addStretch()
        bl.addLayout(proto_row)

        # ── 参数表单 ──
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        spin_slave = QSpinBox()
        spin_slave.setRange(1, 247)
        spin_slave.setValue(1)
        spin_slave.setFont(QFont("Consolas", 9))
        form.addRow("从站地址:", spin_slave)
        lbl_slave = form.labelForField(spin_slave)

        combo_func = QComboBox()
        combo_func.addItems(list(FUNC_CODES.keys()))
        combo_func.setCurrentIndex(2)
        combo_func.setFont(QFont("Consolas", 9))
        form.addRow("功能码:", combo_func)

        spin_addr = QSpinBox()
        spin_addr.setRange(0, 65535)
        spin_addr.setPrefix("0x")
        spin_addr.setDisplayIntegerBase(16)
        spin_addr.setFont(QFont("Consolas", 9))
        form.addRow("起始地址:", spin_addr)

        spin_qty = QSpinBox()
        spin_qty.setRange(1, 125)
        spin_qty.setValue(1)
        spin_qty.setFont(QFont("Consolas", 9))
        form.addRow("数量:", spin_qty)

        edit_data = QLineEdit()
        edit_data.setFont(QFont("Consolas", 9))
        edit_data.setPlaceholderText("例: 0064 00C8")
        form.addRow("数据(HEX):", edit_data)

        bl.addLayout(form)

        # ── 动态显隐 ──
        lbl_qty = form.labelForField(spin_qty)
        lbl_data = form.labelForField(edit_data)

        def sync_protocol_ui():
            show_tcp = radio_tcp.isChecked()
            lbl_slave.setVisible(not show_tcp)
            spin_slave.setVisible(not show_tcp)
            lbl_tcp_unit.setVisible(show_tcp)
            spin_tcp_unit.setVisible(show_tcp)

        proto_group.buttonClicked.connect(lambda: sync_protocol_ui())

        def on_func_changed():
            _, op = FUNC_CODES[combo_func.currentText()]
            is_read = op in ('read_bits', 'read_regs')
            lbl_qty.setVisible(is_read)
            spin_qty.setVisible(is_read)
            lbl_data.setVisible(not is_read)
            edit_data.setVisible(not is_read)
            if is_read:
                spin_qty.setRange(1, 125 if op == 'read_regs' else 2000)

        combo_func.currentIndexChanged.connect(on_func_changed)
        on_func_changed()
        sync_protocol_ui()

        # ── 生成的帧（紧凑布局）──
        result_group = QVBoxLayout()
        result_group.setContentsMargins(0, 0, 0, 0)
        result_group.setSpacing(2)
        result_group.addWidget(QLabel("生成的帧:"))
        result_frame = QTextEdit()
        result_frame.setReadOnly(True)
        result_frame.setFont(QFont("Consolas", 10))
        result_frame.setMinimumHeight(64)
        result_frame.setMaximumHeight(160)
        result_group.addWidget(result_frame)
        bl.addLayout(result_group)

        # ── 构建逻辑 ──
        def build_pdu(slave, func, addr, op):
            raw = bytes([slave, func, (addr >> 8) & 0xFF, addr & 0xFF])
            if op in ('read_bits', 'read_regs'):
                q = spin_qty.value()
                raw += bytes([(q >> 8) & 0xFF, q & 0xFF])
            elif op == 'write_single':
                try:
                    v = int(edit_data.text().strip().replace(' ', ''), 16)
                except ValueError:
                    v = 0
                raw += bytes([(v >> 8) & 0xFF, v & 0xFF])
            elif op == 'write_multi':
                ds = edit_data.text().strip().replace(' ', '')
                try:
                    db = bytes.fromhex(ds)
                except ValueError:
                    QMessageBox.warning(dialog, "格式错误", "数据(HEX) 格式无效")
                    return None
                n = len(db)
                q = n if func == 15 else n // 2
                raw += bytes([(q >> 8) & 0xFF, q & 0xFF, n]) + db
            return raw

        def on_build():
            func, op = FUNC_CODES[combo_func.currentText()]
            addr = spin_addr.value()

            if radio_rtu.isChecked():
                raw = build_pdu(spin_slave.value(), func, addr, op)
                if raw is None: return
                crc = modbus_crc16(raw)
                frame = raw + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
                result_frame.setText(frame.hex(' ').upper())

            elif radio_ascii.isChecked():
                raw = build_pdu(spin_slave.value(), func, addr, op)
                if raw is None: return
                lrc = (-sum(raw)) & 0xFF
                result_frame.setText(':' + raw.hex().upper() + f'{lrc:02X}')

            elif radio_tcp.isChecked():
                unit_id = spin_tcp_unit.value()
                raw = build_pdu(unit_id, func, addr, op)
                if raw is None: return
                if not hasattr(self, 'modbus_tcp_tid'):
                    self.modbus_tcp_tid = 0
                self.modbus_tcp_tid = (self.modbus_tcp_tid + 1) % 65536
                length = len(raw)
                mbap = bytes([
                    (self.modbus_tcp_tid >> 8) & 0xFF, self.modbus_tcp_tid & 0xFF,
                    0, 0,
                    (length >> 8) & 0xFF, length & 0xFF,
                ])
                result_frame.setText((mbap + raw).hex(' ').upper())

        # ── 按钮行 ──
        bbl = QHBoxLayout()
        bbl.setSpacing(4)

        btn_build = QPushButton("构建帧")
        btn_build.clicked.connect(on_build)
        bbl.addWidget(btn_build)

        def copy_frame():
            t = result_frame.toPlainText().strip()
            if t:
                QApplication.clipboard().setText(t)

        bbl.addWidget(QPushButton("复制", clicked=copy_frame))

        def send_frame():
            t = result_frame.toPlainText().strip().replace(' ', '').lstrip(':')
            if radio_ascii.isChecked():
                t = t[:-2]  # 去掉 LRC 校验字节
            if not t:
                return
            if not hasattr(self, 'transport') or not self.transport or not self.transport.is_open:
                QMessageBox.warning(dialog, "提示", "请先打开连接再发送。")
                return
            try:
                data = bytes.fromhex(t)
                with QMutexLocker(self.serial_mutex):
                    self.transport.write(data)
                self.tx_bytes += len(data)
                self.label_tx_bytes.setText(f"发送字节: {self.tx_bytes}")
                self._set_status("Modbus 帧已发送", "ready")
            except Exception as e:
                QMessageBox.critical(dialog, "发送失败", str(e))

        bbl.addWidget(QPushButton("发送", clicked=send_frame))
        bbl.addStretch()
        bl.addLayout(bbl)
        tabs.addTab(build_tab, "帧构建")

        # ═══════════════════════════════════════
        #  Tab 2: 帧解析
        # ═══════════════════════════════════════
        parse_tab = QWidget()
        pl = QVBoxLayout(parse_tab)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.setSpacing(8)

        pl.addWidget(QLabel("输入帧 (HEX):"))
        edit_input = QTextEdit()
        edit_input.setFont(QFont("Consolas", 10))
        edit_input.setPlaceholderText("输入 HEX 帧，例: 01 03 00 00 00 01 84 0A")
        edit_input.setMaximumHeight(56)
        pl.addWidget(edit_input)

        mr = QHBoxLayout()
        mr.addWidget(QLabel("模式:"))
        combo_mode = QComboBox()
        combo_mode.addItems(["自动检测", "RTU", "ASCII"])
        combo_mode.setFont(QFont("Consolas", 9))
        mr.addWidget(combo_mode)
        mr.addStretch()

        btn_parse = QPushButton("解析")
        mr.addWidget(btn_parse)
        pl.addLayout(mr)

        result_parse = QTextEdit()
        result_parse.setReadOnly(True)
        result_parse.setFont(QFont("Consolas", 10))
        pl.addWidget(result_parse)

        def on_parse():
            raw_text = edit_input.toPlainText().strip()
            if not raw_text:
                return
            try:
                clean = raw_text.replace(' ', '').replace('\r', '').replace('\n', '')
                lrc_ok = None
                lrc_byte = None
                mode = combo_mode.currentText()
                if mode == "ASCII" or (mode == "自动检测" and clean.startswith(':')):
                    raw_all = bytes.fromhex(clean.lstrip(':').rstrip('\n'))
                    lrc_byte = raw_all[-1]
                    raw = raw_all[:-1]
                    lrc_ok = (sum(raw) + lrc_byte) & 0xFF == 0
                else:
                    raw = bytes.fromhex(clean)

                if len(raw) < 4:
                    result_parse.setText("错误: 帧长度不足（最少 4 字节）")
                    return

                slave = raw[0]
                func = raw[1]
                is_exc = (func & 0x80) != 0
                lines = [f"从站地址: {slave}"]

                if is_exc:
                    exc_name = EXCEPTIONS.get(raw[2], '未知异常')
                    lines.append(f"功能码: {func} (0x{func:02X}) ← 异常响应")
                    lines.append(f"异常码: {raw[2]} — {exc_name}")
                else:
                    name = next((k for k, v in FUNC_CODES.items() if v[0] == func),
                                f'未知功能码 (0x{func:02X})')
                    lines.append(f"功能码: {name}")
                    op = FUNC_CODES.get(name, (None, ''))[1]

                    if op in ('read_bits', 'read_regs'):
                        byte_count = raw[2]
                        lines.append(f"数据长度: {byte_count} 字节")
                        if op == 'read_bits':
                            for i in range(byte_count):
                                bits = format(raw[3 + i], '08b')
                                lines.append(f"线圈[{i * 8}-{i * 8 + 7}]: {bits}")
                        else:
                            for i in range(byte_count // 2):
                                val = (raw[3 + i * 2] << 8) | raw[3 + i * 2 + 1]
                                lines.append(f"寄存器[{i}]: {val} (0x{val:04X})")
                    elif op == 'write_single':
                        val = (raw[4] << 8) | raw[5]
                        lines.append(f"地址: {(raw[2] << 8) | raw[3]}")
                        lines.append(f"值: {val} (0x{val:04X})")
                    elif op == 'write_multi':
                        lines.append(f"起始地址: {(raw[2] << 8) | raw[3]}")
                        lines.append(f"数量: {(raw[4] << 8) | raw[5]}")

                # CRC / LRC 校验
                if len(raw) >= 6 and not is_exc:
                    payload = raw[:-2]
                    expected = modbus_crc16(payload)
                    actual = (raw[-1] << 8) | raw[-2]
                    ok = "✅ 校验通过" if expected == actual else f"❌ 校验失败 (应为 0x{expected:04X})"
                    lines.append(f"CRC: 0x{actual:04X}  {ok}")
                elif lrc_ok is not None and lrc_byte is not None:
                    ok = "✅ 校验通过" if lrc_ok else "❌ 校验失败"
                    lines.append(f"LRC: 0x{lrc_byte:02X}  {ok}")

                result_parse.setText('\n'.join(lines))
            except (ValueError, IndexError) as e:
                result_parse.setText(f"解析错误: {str(e) or 'HEX 格式无效'}")

        btn_parse.clicked.connect(on_parse)
        tabs.addTab(parse_tab, "帧解析")

        # ── 底部 ──
        bottom = QHBoxLayout()
        bottom.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dialog.close)
        bottom.addWidget(btn_close)
        main_layout.addLayout(bottom)

        self._apply_dialog_theme(dialog)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def data_viewer(self):
        """数据分析面板：监听串口/网络流，实时捕获和可视化 JSON 数据"""
        from data_viewer import JsonViewerDialog  # 懒加载，避免启动时导入 pyqtgraph/numpy
        if hasattr(self, '_json_viewer_dlg') and self._json_viewer_dlg is not None:
            try:
                self._json_viewer_dlg.set_theme(is_dark=(self.current_theme == 'dark'))
                self._json_viewer_dlg.show()
                self._json_viewer_dlg.raise_()
                self._json_viewer_dlg.activateWindow()
                return
            except RuntimeError:
                self._json_viewer_dlg = None

        arrow_paths = {
            'dark': getattr(self, '_arrow_dark_path', ''),
            'light': getattr(self, '_arrow_light_path', ''),
        }
        dlg = JsonViewerDialog(None, theme_callback=self._apply_dialog_theme, arrow_paths=arrow_paths,
                              is_dark=(self.current_theme == 'dark'))
        self._json_viewer_dlg = dlg

        # 连接串口数据信号
        if hasattr(self, 'read_thread') and self.read_thread:
            self.read_thread.receive_data_signal.connect(dlg.feed_raw_data)

        # 对话框关闭时的清理
        def on_finished():
            if hasattr(self, 'read_thread') and self.read_thread:
                try:
                    self.read_thread.receive_data_signal.disconnect(dlg.feed_raw_data)
                except (TypeError, RuntimeError):
                    pass
            if self._json_viewer_dlg is dlg:
                self._json_viewer_dlg = None

        dlg.finished.connect(on_finished)
        dlg.set_theme(is_dark=(self.current_theme == 'dark'))
        self._apply_dialog_theme(dlg)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    def show_usage(self):
        """显示使用说明"""
        show_usage_dialog(self, is_dark=(self.current_theme == 'dark'))

    def show_about(self):
        """显示关于对话框"""
        show_about_dialog(self, is_dark=(self.current_theme == 'dark'))

    def open_ota_center(self):
        """打开 OTA 升级控制中心对话框。"""
        from ota_center import OTAControlCenter  # 懒加载，避免启动时导入 http.server 等重型依赖
        # 如果已有实例则激活并显示，否则创建新实例
        if hasattr(self, '_ota_dialog') and self._ota_dialog is not None:
            try:
                self._ota_dialog.show()
                self._ota_dialog.raise_()
                self._ota_dialog.activateWindow()
                return
            except RuntimeError:
                self._ota_dialog = None

        self._ota_dialog = OTAControlCenter(self)
        self._apply_dialog_theme(self._ota_dialog)
        self._ota_dialog.show()

    def _apply_dialog_theme(self, dialog):
        """为子对话框应用当前主题（暗黑/明亮），支持运行时切换。"""
        is_dark = self.current_theme == 'dark'
        apply_dialog_theme(dialog, is_dark)

    def show_more_settings(self):
        """显示更多设置（串口模式）"""
        try:
            # 创建设置对话框
            dialog = QDialog(self)
            dialog.setWindowTitle("Setup")
            dialog.setMinimumSize(300, 250)
            # 移除右上角的问号帮助按钮
            dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            
            layout = QVBoxLayout(dialog)
            
            # 设置组
            settings_group = QGroupBox("串口设置")
            settings_layout = QFormLayout(settings_group)
            
            # 端口选择
            combo_port_setup = QComboBox()
            # 填充可用串口
            try:
                from serial.tools import list_ports
                ports = list_ports.comports()
                port_list = [port.device for port in ports]
                if port_list:
                    combo_port_setup.addItems(port_list)
                    # 默认选择当前端口
                    current_port = self.combo_port.currentText()
                    if current_port in port_list:
                        combo_port_setup.setCurrentText(current_port)
                else:
                    combo_port_setup.addItem("无可用串口")
            except Exception as e:
                combo_port_setup.addItem("无可用串口")
                self.append_text(f"[错误]: 枚举串口失败: {e}\n")
            settings_layout.addRow("端口", combo_port_setup)
            
            # 波特率选择
            combo_baud_setup = QComboBox()
            baud_rates = ['9600', '19200', '38400', '57600', '115200', '230400', '460800', '921600']
            combo_baud_setup.addItems(baud_rates)
            # 默认选择当前波特率
            current_baud = self.combo_baud.currentText()
            if current_baud in baud_rates:
                combo_baud_setup.setCurrentText(current_baud)
            else:
                combo_baud_setup.setCurrentText('115200')
            settings_layout.addRow("波特率", combo_baud_setup)
            
            # 数据位选择
            combo_data_bits = QComboBox()
            combo_data_bits.addItems(['5', '6', '7', '8'])
            current_data_bits = getattr(self, 'serial_data_bits', '8')
            combo_data_bits.setCurrentText(current_data_bits if current_data_bits in ['5', '6', '7', '8'] else '8')
            settings_layout.addRow("数据位", combo_data_bits)

            # 停止位选择
            combo_stop_bits = QComboBox()
            combo_stop_bits.addItems(['1', '1.5', '2'])
            current_stop_bits = getattr(self, 'serial_stop_bits', '1')
            combo_stop_bits.setCurrentText(current_stop_bits if current_stop_bits in ['1', '1.5', '2'] else '1')
            settings_layout.addRow("停止位", combo_stop_bits)
            
            # 校验位选择（显示中文，内部使用英文值）
            parity_display = ['无', '偶校验', '奇校验', '标记', '空格']
            parity_internal = ['None', 'Even', 'Odd', 'Mark', 'Space']
            combo_parity = QComboBox()
            combo_parity.addItems(parity_display)
            # 根据当前内部值设置显示文本
            current_parity = getattr(self, 'serial_parity', 'None')
            try:
                idx = parity_internal.index(current_parity)
                combo_parity.setCurrentIndex(idx)
            except ValueError:
                combo_parity.setCurrentIndex(0)
            settings_layout.addRow("校验位", combo_parity)

            # 流控制选择（显示中文，内部使用英文值）
            flow_display = ['无', '软件流控', '硬件流控(RTS/CTS)', '硬件流控(DSR/DTR)']
            flow_internal = ['None', 'Xon/Xoff', 'RTS/CTS', 'DSR/DTR']
            combo_flow_control = QComboBox()
            combo_flow_control.addItems(flow_display)
            # 根据当前内部值设置显示文本
            current_flow = getattr(self, 'serial_flow_control', 'None')
            try:
                idx = flow_internal.index(current_flow)
                combo_flow_control.setCurrentIndex(idx)
            except ValueError:
                combo_flow_control.setCurrentIndex(0)
            settings_layout.addRow("流控制", combo_flow_control)
            
            layout.addWidget(settings_group)
            
            # 按钮布局
            button_layout = QHBoxLayout()
            button_layout.addStretch()
            
            # 确定按钮
            btn_ok = QPushButton("确定")
            btn_ok.clicked.connect(dialog.accept)
            button_layout.addWidget(btn_ok)
            
            # 取消按钮
            btn_cancel = QPushButton("取消")
            btn_cancel.clicked.connect(dialog.reject)
            button_layout.addWidget(btn_cancel)
            
            layout.addLayout(button_layout)
            
            # 显示对话框
            self._apply_dialog_theme(dialog)
            if dialog.exec_() == QDialog.Accepted:
                # 应用设置
                selected_port = combo_port_setup.currentText()
                selected_baud = combo_baud_setup.currentText()
                
                # 保存设置的值到实例变量
                self.serial_data_bits = combo_data_bits.currentText()
                self.serial_stop_bits = combo_stop_bits.currentText()
                # 将显示文本转换为内部英文值
                parity_idx = combo_parity.currentIndex()
                if parity_idx >= 0 and parity_idx < len(parity_internal):
                    self.serial_parity = parity_internal[parity_idx]
                else:
                    self.serial_parity = 'None'
                flow_idx = combo_flow_control.currentIndex()
                if flow_idx >= 0 and flow_idx < len(flow_internal):
                    self.serial_flow_control = flow_internal[flow_idx]
                else:
                    self.serial_flow_control = 'None'
                
                # 更新主界面的端口和波特率
                if selected_port != "无可用串口":
                    # 查找端口在主界面的索引
                    index = self.combo_port.findText(selected_port)
                    if index != -1:
                        self.combo_port.setCurrentIndex(index)
                
                # 查找波特率在主界面的索引
                index = self.combo_baud.findText(selected_baud)
                if index != -1:
                    self.combo_baud.setCurrentIndex(index)
                else:
                    # 波特率不在列表中，插入到"自定义"之前
                    custom_index = self.combo_baud.findText('自定义')
                    if custom_index != -1:
                        self.combo_baud.currentIndexChanged.disconnect(self.handle_baud_change)
                        self.combo_baud.insertItem(custom_index, selected_baud)
                        self.combo_baud.setCurrentIndex(custom_index)
                        self.combo_baud.currentIndexChanged.connect(self.handle_baud_change)
        except Exception as e:
            self.append_text(f"[错误]: 打开设置对话框失败: {e}\n")
            # 显示错误提示
            QMessageBox.critical(self, "错误", f"打开设置对话框失败: {e}")

    def create_new_log_file(self):
        """创建新的日志文件"""
        import os
        from PyQt5.QtCore import QDateTime
        
        # 关闭当前文件（如果存在）
        if self.current_log_file:
            try:
                self.current_log_file.close()
            except Exception as e:
                self.append_text(f"[错误]: 关闭旧日志文件失败: {str(e)}\n")
        
        # 获取当前时间作为文件名的一部分
        current_time = QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss")
        self.log_file_count += 1
        
        # 创建默认文件名
        default_filename = f"serial_data_{current_time}_{self.log_file_count}.txt"
        
        try:
            # 确保保存目录存在
            if not os.path.exists(self.save_directory):
                os.makedirs(self.save_directory)
            
            # 检查磁盘空间
            import platform
            if platform.system() == 'Windows':
                # Windows系统使用ctypes获取磁盘空间
                import ctypes
                free_bytes = ctypes.c_ulonglong(0)
                total_bytes = ctypes.c_ulonglong(0)
                drive = os.path.splitdrive(self.save_directory)[0] + '\\'
                ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(drive), None, ctypes.pointer(total_bytes), ctypes.pointer(free_bytes))
                free_space = free_bytes.value
            else:
                # Unix系统使用statvfs
                stat = os.statvfs(self.save_directory)
                free_space = stat.f_bavail * stat.f_frsize
            
            if free_space < 100 * 1024 * 1024:  # 至少需要100MB自由空间
                raise Exception(f"磁盘空间不足，剩余空间: {free_space / (1024*1024):.2f}MB")
            
            # 构建文件路径
            self.log_file_path = os.path.join(self.save_directory, default_filename)
            
            # 验证文件路径
            if not os.path.isdir(os.path.dirname(self.log_file_path)):
                raise Exception("文件路径无效")
            
            # 检查是否需要备份当前文件（如果存在）
            if self.current_log_file and self.log_file_path:
                worker = FileOperationWorker(self.backup_current_file)
                self.thread_pool.start(worker)
            
            # 打开文件
            self.current_log_file = open(self.log_file_path, 'w', encoding='utf-8')
            self.log_file_size = 0
            self.append_text(f"[系统]: 已创建新的日志文件: {self.log_file_path}\n")
            # 更新状态栏
            self.status_log.setText(f"日志文件: {os.path.basename(self.log_file_path)}")
            self._set_status(f"已创建新的日志文件: {os.path.basename(self.log_file_path)}")

        except Exception as e:
            error_msg = f"创建日志文件失败: {str(e)}"
            self.append_text(f"[错误]: {error_msg}\n")
            self.current_log_file = None
            self.log_file_path = ""
            self.log_file_size = 0
            # 更新状态栏
            self.status_log.setText("日志文件: 创建失败")
            self._set_status(error_msg, "error")

    def _check_disk_space(self, required_bytes=1024):
        """检查磁盘空间是否充足"""
        import os
        import platform
        
        try:
            if platform.system() == 'Windows':
                import ctypes
                free_bytes = ctypes.c_ulonglong(0)
                drive = os.path.splitdrive(self.save_directory)[0] + '\\'
                ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(drive), None, None, ctypes.pointer(free_bytes))
                free_space = free_bytes.value
            else:
                stat = os.statvfs(self.save_directory)
                free_space = stat.f_bavail * stat.f_frsize
            
            return free_space >= required_bytes
        except Exception as e:
            print(f"检查磁盘空间失败: {e}")
            return False
    
    def _fsync_log_file(self):
        """定时强制把日志文件缓冲区落盘（由 _log_fsync_timer 周期触发）。

        单独存放是为了让 fsync 这种慢操作脱离数据接收主路径，避免阻塞 UI。
        """
        if not self.current_log_file:
            return
        try:
            self.current_log_file.flush()
            os.fsync(self.current_log_file.fileno())
        except (OSError, IOError, ValueError):
            # 文件可能已被关闭或句柄无效，忽略即可
            pass
        except Exception as e:
            print(f"定时刷盘失败: {e}")

    def auto_save_data(self, data):
        """自动保存数据到文件"""
        try:
            # 检查自动保存是否启用
            if not self.auto_save_enabled:
                return
            
            # 检查当前日志文件是否存在
            if not self.current_log_file:
                self.create_new_log_file()
                if not self.current_log_file:
                    return
            
            # 确保数据是字符串类型
            if not isinstance(data, str):
                data = str(data)
            data_size = len(data.encode('utf-8'))

            # 检查文件大小是否超过限制
            if self.log_file_size + data_size > self.max_log_file_size:
                # 创建新文件（会自动关闭旧文件）
                self.create_new_log_file()
                if not self.current_log_file:
                    return

            # 写入前再次检查磁盘空间（防止其他进程占用空间）
            if not self._check_disk_space(data_size + 1024):
                error_msg = "磁盘空间不足，无法写入数据"
                self.append_text(f"[错误]: {error_msg}\n")
                self._set_status(error_msg, "error")
                return

            # 写入数据（使用 try-except-else-finally 确保文件句柄安全）
            # 注意：此处只调用 flush（刷新 Python 缓冲到 OS，不阻塞），
            # fsync 由 _log_fsync_timer 定时执行，避免每条数据都阻塞 UI 线程
            write_success = False
            try:
                self.current_log_file.write(data)
                self.current_log_file.flush()  # 刷新到操作系统缓冲区（非阻塞）
                write_success = True
            except IOError as e:
                # 处理I/O错误（包括磁盘空间不足）
                error_msg = f"写入文件失败: {str(e)}"
                self.append_text(f"[错误]: {error_msg}\n")
            except Exception as e:
                # 处理其他错误
                error_msg = f"自动保存失败: {str(e)}"
                self.append_text(f"[错误]: {error_msg}\n")
            finally:
                if write_success:
                    self.log_file_size += data_size
                else:
                    # 写入失败，关闭当前文件句柄并尝试重新创建
                    if self.current_log_file:
                        try:
                            self.current_log_file.close()
                        except Exception:
                            pass
                        finally:
                            self.current_log_file = None
                    # 尝试重新创建文件
                    self.create_new_log_file()
        except Exception as e:
            # 处理外层错误
            error_msg = f"自动保存过程出错: {str(e)}"
            self.append_text(f"[错误]: {error_msg}\n")
            # 确保文件句柄被正确关闭
            if self.current_log_file:
                try:
                    self.current_log_file.close()
                except Exception:
                    pass
                finally:
                    self.current_log_file = None
            # 更新状态栏
            self._set_status(error_msg, "error")



    def rollover_log_files(self):
        """执行日志文件滚动，删除旧的日志文件"""
        import os
        
        # 获取保存目录中的所有日志文件
        log_files = []
        try:
            for filename in os.listdir(self.save_directory):
                if filename.startswith("serial_data_") and filename.endswith(".txt"):
                    file_path = os.path.join(self.save_directory, filename)
                    if os.path.isfile(file_path):
                        # 获取文件的修改时间
                        mtime = os.path.getmtime(file_path)
                        log_files.append((mtime, file_path))
        except Exception as e:
            self.append_text(f"[错误]: 读取日志文件列表失败: {str(e)}\n")
            return
        
        # 按修改时间排序，旧的在前
        log_files.sort()
        
        # 删除超出限制的旧文件
        while len(log_files) >= self.max_log_files:
            oldest_file = log_files.pop(0)
            try:
                os.remove(oldest_file[1])
                self.append_text(f"[系统]: 已删除旧日志文件: {os.path.basename(oldest_file[1])}\n")
            except Exception as e:
                self.append_text(f"[错误]: 删除旧日志文件失败: {str(e)}\n")

    def backup_current_file(self):
        """备份当前日志文件"""
        import os
        import shutil
        
        if not os.path.exists(self.log_file_path):
            return
        
        try:
            # 创建备份文件名
            backup_path = self.log_file_path + ".bak"
            # 复制文件
            shutil.copy2(self.log_file_path, backup_path)
            self.append_text(f"[系统]: 已备份日志文件到: {os.path.basename(backup_path)}\n")
        except Exception as e:
            self.append_text(f"[错误]: 备份日志文件失败: {str(e)}\n")

    def load_config(self):
        """加载配置文件"""
        import json
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    # 限制读取的字节数，防止超大配置文件导致内存问题
                    max_config_size = 1024 * 1024  # 1MB
                    content = f.read(max_config_size)
                    if len(content) == max_config_size:
                        print("[警告]: 配置文件过大，可能已被截断")
                
                # 解析JSON并验证结构
                try:
                    config = json.loads(content)
                except json.JSONDecodeError as e:
                    print(f"[错误]: 配置文件格式无效: {e}")
                    return
                
                # 验证配置是否为字典类型
                if not isinstance(config, dict):
                    print("[错误]: 配置文件必须是JSON对象")
                    return
                
                if 'connection_mode' in config and config['connection_mode'] in ('serial', 'udp', 'tcp_client', 'tcp_server'):
                    self.connection_mode = config['connection_mode']
                    mode_map = {'serial': 0, 'udp': 1, 'tcp_client': 2, 'tcp_server': 3}
                    idx = mode_map.get(self.connection_mode, 0)
                    if hasattr(self, 'combo_mode'):
                        self.combo_mode.blockSignals(True)
                        self.combo_mode.setCurrentIndex(idx)
                        self.combo_mode.blockSignals(False)
                    if hasattr(self, 'stack_params'):
                        self.stack_params.setCurrentIndex(idx)
                    is_serial = (self.connection_mode == 'serial')
                    if hasattr(self, 'check_rts'): self.check_rts.setVisible(is_serial)
                    if hasattr(self, 'check_dtr'): self.check_dtr.setVisible(is_serial)
                    if hasattr(self, 'btn_more_settings'): self.btn_more_settings.setEnabled(is_serial)

                if 'port' in config and isinstance(config['port'], str):
                    port_index = self.combo_port.findText(config['port'])
                    if port_index >= 0:
                        self.combo_port.setCurrentIndex(port_index)

                if 'baudrate' in config and isinstance(config['baudrate'], (int, str)):
                    baud_index = self.combo_baud.findText(str(config['baudrate']))
                    if baud_index >= 0:
                        self.combo_baud.setCurrentIndex(baud_index)

                if 'udp_local_ip' in config and hasattr(self, 'edit_udp_local_ip'):
                    self.edit_udp_local_ip.setCurrentText(str(config['udp_local_ip']))
                if 'udp_local_port' in config and hasattr(self, 'edit_udp_local_port'):
                    try: self.edit_udp_local_port.setValue(int(config['udp_local_port']))
                    except: self.edit_udp_local_port.setValue(8080)
                if 'udp_remote_ip' in config and hasattr(self, 'edit_udp_remote_ip'):
                    self.edit_udp_remote_ip.setText(str(config['udp_remote_ip']))
                if 'udp_remote_port' in config and hasattr(self, 'edit_udp_remote_port'):
                    try: self.edit_udp_remote_port.setValue(int(config['udp_remote_port']))
                    except: self.edit_udp_remote_port.setValue(8888)
                if 'tcp_remote_ip' in config and hasattr(self, 'edit_tcp_remote_ip'):
                    self.edit_tcp_remote_ip.setText(str(config['tcp_remote_ip']))
                if 'tcp_remote_port' in config and hasattr(self, 'edit_tcp_remote_port'):
                    try: self.edit_tcp_remote_port.setValue(int(config['tcp_remote_port']))
                    except: self.edit_tcp_remote_port.setValue(8888)
                if 'tcp_server_local_ip' in config and hasattr(self, 'edit_tcp_server_local_ip'):
                    self.edit_tcp_server_local_ip.setCurrentText(str(config['tcp_server_local_ip']))
                if 'tcp_server_local_port' in config and hasattr(self, 'edit_tcp_server_local_port'):
                    try: self.edit_tcp_server_local_port.setValue(int(config['tcp_server_local_port']))
                    except: self.edit_tcp_server_local_port.setValue(8888)
                
                # 加载自动保存设置
                if 'auto_save' in config and isinstance(config['auto_save'], bool):
                    self.check_auto_save.setChecked(config['auto_save'])
                
                # 加载保存路径（验证路径安全性）
                if 'save_directory' in config and isinstance(config['save_directory'], str):
                    # 验证路径安全性
                    if self.is_valid_path(config['save_directory']):
                        self.save_directory = config['save_directory']
                        self.line_edit_save_path.setText(self.save_directory)
                    else:
                        print(f"[警告]: 保存路径不安全，已忽略: {config['save_directory']}")
                
                # 加载HEX显示设置
                if 'hex_recv' in config:
                    self.check_hex_recv.setChecked(config['hex_recv'])
                
                # 加载HEX发送设置
                if 'hex_send' in config:
                    self.check_hex_send.setChecked(config['hex_send'])
                
                # 加载显示时间戳设置
                if 'show_timestamp' in config:
                    self.act_timestamp.setChecked(config['show_timestamp'])
                    self.check_timestamp.blockSignals(True)
                    self.check_timestamp.setChecked(config['show_timestamp'])
                    self.check_timestamp.blockSignals(False)
                
                # 加载首字段
                if 'head_field' in config:
                    self.text_ota.setText(config['head_field'])
                
                # 加载尾字段
                if 'tail_field' in config:
                    self.text_tail.setText(config['tail_field'])
                
                # 加载首字段勾选状态
                if 'head_field_enabled' in config:
                    self.check_head_field.setChecked(config['head_field_enabled'])
                
                # 加载尾字段勾选状态
                if 'tail_field_enabled' in config:
                    self.check_tail_field.setChecked(config['tail_field_enabled'])
                
                # 加载回车换行设置
                if 'newline' in config:
                    self.check_newline.setChecked(config['newline'])
                
                # 加载RTS设置
                if 'rts' in config:
                    self.check_rts.setChecked(config['rts'])
                
                # 加载DTR设置
                if 'dtr' in config:
                    self.check_dtr.setChecked(config['dtr'])
                
                # 加载更多串口设置参数
                if 'data_bits' in config:
                    self.serial_data_bits = config['data_bits']
                if 'stop_bits' in config:
                    self.serial_stop_bits = config['stop_bits']
                if 'parity' in config:
                    self.serial_parity = config['parity']
                if 'flow_control' in config:
                    self.serial_flow_control = config['flow_control']
                
                # 加载编码格式
                if 'encoding' in config:
                    encoding = config['encoding']
                    encoding_index = self.combo_encoding.findText(encoding)
                    if encoding_index >= 0:
                        self.combo_encoding.setCurrentIndex(encoding_index)
                    else:
                        print(f"未找到保存的编码格式: {encoding}，使用默认UTF-8")
                
                # 加载多字符发送项目
                if 'multi_items' in config and isinstance(config['multi_items'], list):
                    # 清空现有项目
                    self.table_multi_send.setRowCount(0)
                    
                    # 添加保存的项目
                    for item_data in config['multi_items']:
                        if not isinstance(item_data, dict):
                            continue
                        
                        row = self.table_multi_send.rowCount()
                        self.table_multi_send.insertRow(row)
                        
                        # HEX复选框
                        hex_checkbox = QCheckBox()
                        hex_checkbox.setChecked(item_data.get('hex', False))
                        hex_widget = QWidget()
                        hex_layout = QHBoxLayout(hex_widget)
                        hex_layout.setContentsMargins(0, 0, 0, 0)
                        hex_layout.setAlignment(Qt.AlignCenter)
                        hex_layout.addWidget(hex_checkbox)
                        self.table_multi_send.setCellWidget(row, 0, hex_widget)

                        # 字符串输入框
                        string_item = QTableWidgetItem(item_data.get('string', ''))
                        self.table_multi_send.setItem(row, 1, string_item)

                        # 发送按钮
                        button = QPushButton(item_data.get('button_text', '无注释'))
                        button.setFont(QFont("Microsoft YaHei", 9))
                        button.setMinimumWidth(70)
                        button.clicked.connect(self.on_send_multi_btn_clicked)
                        # 为按钮设置唯一的对象名称，用于事件过滤器
                        button.setObjectName(f"btn_{row}")
                        button.installEventFilter(self)
                        button_widget = QWidget()
                        button_layout = QHBoxLayout(button_widget)
                        button_layout.setContentsMargins(0, 0, 0, 0)
                        button_layout.setAlignment(Qt.AlignCenter)
                        button_layout.addWidget(button)
                        self.table_multi_send.setCellWidget(row, 2, button_widget)

                        # 延时设置
                        delay_spin = QSpinBox()
                        delay_spin.setRange(0, 10000)
                        try:
                            delay = int(item_data.get('delay', 1000))
                            if delay < 0:
                                delay = 1000
                        except (ValueError, TypeError):
                            delay = 1000
                        delay_spin.setValue(delay)
                        delay_spin.setFont(QFont("Consolas", 9))
                        delay_widget = QWidget()
                        delay_layout = QHBoxLayout(delay_widget)
                        delay_layout.setContentsMargins(0, 0, 0, 0)
                        delay_layout.setAlignment(Qt.AlignCenter)
                        delay_layout.addWidget(delay_spin)
                        self.table_multi_send.setCellWidget(row, 3, delay_widget)
                        
                        # 顺序显示框
                        order_item = QTableWidgetItem(str(item_data.get('order', row + 1)))
                        order_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        order_item.setTextAlignment(Qt.AlignCenter)  # 文本居中显示
                        self.table_multi_send.setItem(row, 4, order_item)
                
                # 加载发送历史记录
                if 'send_history' in config and isinstance(config['send_history'], list):
                    for item in config['send_history']:
                        if isinstance(item, str) and item.strip():
                            self.send_history.append(item)
                    # 历史记录已限制为 maxlen=30 条

                # 加载主题设置
                need_apply_theme = False
                if 'theme' in config and config['theme'] in ('light', 'dark'):
                    if config['theme'] != self.current_theme:
                        self.current_theme = config['theme']
                        self.theme_colors = dict(THEME_COLORS[self.current_theme])
                        need_apply_theme = True

                # 如果加载了与默认不同的主题，重新应用
                if need_apply_theme:
                    self.apply_theme(self.current_theme)

                self.append_text("[系统]: 配置已加载\n")
            except Exception as e:
                self.append_text(f"[错误]: 加载配置失败: {str(e)}\n")

    def save_config(self):
        """保存配置文件"""
        import json
        
        # 获取波特率，添加异常处理
        try:
            baudrate = int(self.combo_baud.currentText())
        except ValueError:
            baudrate = 115200  # 默认值
        
        # 保存多字符发送项目
        multi_items = []
        for i in range(self.table_multi_send.rowCount()):
            try:
                # 获取HEX复选框状态（添加空检查）
                hex_widget = self.table_multi_send.cellWidget(i, 0)
                is_hex = False
                if hex_widget:
                    hex_layout = hex_widget.layout()
                    if hex_layout and hex_layout.count() > 0:
                        hex_checkbox = hex_layout.itemAt(0).widget()
                        if hex_checkbox:
                            is_hex = hex_checkbox.isChecked()
                
                # 获取字符串
                string_item = self.table_multi_send.item(i, 1)
                string = string_item.text() if string_item else ""
                
                # 获取按钮文本（添加空检查）
                button_text = ""
                button_widget = self.table_multi_send.cellWidget(i, 2)
                if button_widget:
                    button_layout = button_widget.layout()
                    if button_layout and button_layout.count() > 0:
                        button = button_layout.itemAt(0).widget()
                        if button:
                            button_text = button.text()
                
                # 获取延时（添加空检查）
                delay = 0
                delay_widget = self.table_multi_send.cellWidget(i, 3)
                if delay_widget:
                    delay_layout = delay_widget.layout()
                    if delay_layout and delay_layout.count() > 0:
                        delay_spin = delay_layout.itemAt(0).widget()
                        if delay_spin:
                            delay = delay_spin.value()
                
                # 获取顺序
                order_item = self.table_multi_send.item(i, 4)
                order = order_item.text() if order_item else ""
                
                multi_items.append({
                    'hex': is_hex,
                    'string': string,
                    'button_text': button_text,
                    'delay': delay,
                    'order': order
                })
            except Exception as e:
                print(f"保存第 {i} 行配置时出错: {e}")
                continue
        
        config = {
            'connection_mode': getattr(self, 'connection_mode', 'serial'),
            'port': self.combo_port.currentText(),
            'baudrate': baudrate,
            'udp_local_ip': self.edit_udp_local_ip.currentText().strip() if hasattr(self, 'edit_udp_local_ip') else '0.0.0.0',
            'udp_local_port': self.edit_udp_local_port.value() if hasattr(self, 'edit_udp_local_port') else 8080,
            'udp_remote_ip': self.edit_udp_remote_ip.text().strip() if hasattr(self, 'edit_udp_remote_ip') else '192.168.1.100',
            'udp_remote_port': self.edit_udp_remote_port.value() if hasattr(self, 'edit_udp_remote_port') else 8888,
            'tcp_remote_ip': self.edit_tcp_remote_ip.text().strip() if hasattr(self, 'edit_tcp_remote_ip') else '192.168.1.100',
            'tcp_remote_port': self.edit_tcp_remote_port.value() if hasattr(self, 'edit_tcp_remote_port') else 8888,
            'tcp_server_local_ip': self.edit_tcp_server_local_ip.currentText().strip() if hasattr(self, 'edit_tcp_server_local_ip') else '0.0.0.0',
            'tcp_server_local_port': self.edit_tcp_server_local_port.value() if hasattr(self, 'edit_tcp_server_local_port') else 8888,
            'auto_save': self.check_auto_save.isChecked(),
            'save_directory': self.save_directory,
            'hex_recv': self.check_hex_recv.isChecked(),
            'hex_send': self.check_hex_send.isChecked(),
            'show_timestamp': self.act_timestamp.isChecked(),
            'newline': self.check_newline.isChecked(),
            'rts': self.check_rts.isChecked(),
            'dtr': self.check_dtr.isChecked(),
            'head_field': self.text_ota.text(),
            'tail_field': self.text_tail.text(),
            'head_field_enabled': self.check_head_field.isChecked(),
            'tail_field_enabled': self.check_tail_field.isChecked(),
            'multi_items': multi_items,
            'send_history': list(self.send_history),  # 发送历史记录（最多30条）
            # 更多串口设置参数
            'data_bits': getattr(self, 'serial_data_bits', '8'),
            'stop_bits': getattr(self, 'serial_stop_bits', '1'),
            'parity': getattr(self, 'serial_parity', 'None'),
            'flow_control': getattr(self, 'serial_flow_control', 'None'),
            # 编码格式
            'encoding': self.combo_encoding.currentText(),
            # 主题设置
            'theme': self.current_theme,
        }
        
        # 保留已有的 OTA 配置（由 OTAControlCenter 独立管理）
        existing = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception:
                pass
        if isinstance(existing, dict) and 'ota' in existing:
            config['ota'] = existing['ota']

        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            # 同步到基础配置文件，供下次新实例继承
            try:
                import shutil as _shutil
                _shutil.copy2(self.config_file, self._base_config_file)
            except Exception:
                pass
            self.append_text("[系统]: 配置已保存\n")
        except Exception as e:
            self.append_text(f"[错误]: 保存配置失败: {str(e)}\n")
    
    def __del__(self):
        """析构函数，确保资源清理"""
        try:
            # 停止批量发送线程
            if hasattr(self, 'batch_thread'):
                try:
                    if self.batch_thread and self.batch_thread.isRunning():
                        self.batch_thread.stop()
                except RuntimeError:
                    pass
                except Exception:
                    pass
            
            # 停止接收线程
            if hasattr(self, 'read_thread'):
                try:
                    if self.read_thread and self.read_thread.isRunning():
                        self.read_thread.stop()
                except RuntimeError:
                    pass
                except Exception:
                    pass
        except Exception:
            pass
        
        # 关闭连接
        if hasattr(self, 'transport') and self.transport and self.transport.is_open:
            try:
                self.transport.close()
            except:
                pass
        
        # 关闭日志文件
        if hasattr(self, 'current_log_file') and self.current_log_file:
            try:
                self.current_log_file.close()
            except:
                pass
        
        # 关闭线程池
        if hasattr(self, 'thread_pool'):
            try:
                self.thread_pool.clear()
                self.thread_pool.waitForDone()
            except:
                pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SerialTool()
    window.show()
    sys.exit(app.exec_())
