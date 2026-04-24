import sys
import os
import serial
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QPushButton, 
                             QTextEdit, QCheckBox, QMessageBox, QSplitter, QSpinBox, QLineEdit, QProgressBar, QGroupBox, QDialog, QFormLayout, 
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QInputDialog, QFrame, QSizePolicy)  
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QEvent, QRunnable, QThreadPool, QObject, QMetaObject, Q_ARG, pyqtSlot
from PyQt5.QtGui import QFont, QTextCursor, QTextCharFormat, QColor

# --- 文件操作工作类 ---
class FileOperationWorker(QRunnable):
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            self.func(*self.args, **self.kwargs)
        except Exception as e:
            print(f"文件操作错误: {e}")

# --- 串口接收线程 ---
class SerialReadThread(QThread):
    # 定义一个信号，用于将接收到的数据传递给主界面
    receive_data_signal = pyqtSignal(bytes)
    # 定义一个信号，用于将错误信息传递给主界面
    error_signal = pyqtSignal(str)

    def __init__(self, serial_port):
        super().__init__()
        self.serial_port = serial_port
        self.running = True

    def run(self):

        """
        线程运行的主函数，持续循环执行直到running标志为False
        负责从串口读取数据，并通过信号发射给其他组件
        """
        try:
            while self.running:  # 当running为True时持续循环
                try:
                    # 检查串口是否仍然打开
                    if not self.serial_port or not self.serial_port.is_open:
                        error_msg = "串口已关闭"
                        self.error_signal.emit(error_msg)
                        self.running = False
                        break
                    
                    # 等待读取数据，超时时间设为0.1秒，以便能及时检查停止标志
                    if self.serial_port.in_waiting > 0:  # 检查串口是否有数据等待读取
                        try:
                            data = self.serial_port.read(self.serial_port.in_waiting)  # 读取所有等待的数据
                            if data:
                                self.receive_data_signal.emit(data)
                        except serial.SerialTimeoutException:
                            # 超时异常，继续循环
                            pass
                        except serial.SerialException as e:
                            # 串口异常，需要停止线程
                            error_msg = f"串口读取异常: {e}"
                            self.error_signal.emit(error_msg)
                            self.running = False
                            break
                    else:
                        self.msleep(50) # 增加休眠时间，降低CPU占用
                except serial.SerialException as e:
                    error_msg = f"串口错误: {e}"
                    self.error_signal.emit(error_msg)
                    self.running = False
                except Exception as e:
                    error_msg = f"读取错误: {e}"
                    print(error_msg)
                    self.error_signal.emit(error_msg)
                    # 判断是否为严重错误，需要停止线程
                    if isinstance(e, (KeyboardInterrupt, SystemExit, MemoryError)):
                        # 严重错误，停止线程
                        self.running = False
                        break
                    else:
                        # 非致命错误，继续运行
                        self.msleep(100)
        finally:
            # 确保线程能够正确停止
            self.running = False

    def stop(self):
        self.running = False
        self.wait()

# --- 主窗口类 ---
class SerialTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.serial_port = None
        self.read_thread = None
        # 日志缓冲区，用于存储所有接收到的数据
        self.log_buffer = []
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
        
        # 配置文件路径
        self.config_file = os.path.join(os.getcwd(), "serial_config.json")
        
        self.init_ui()
        self.refresh_ports() # 启动时刷新串口列表
        self.load_config() # 启动时加载配置

    def init_ui(self):
        self.setWindowTitle("串口调试助手")
        self.resize(1000, 900)

        # 主窗口部件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # --- 顶部设置区域 ---
        # 串口设置分组
        serial_group = QGroupBox("串口设置")
        serial_group.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        serial_layout = QVBoxLayout(serial_group)
        serial_layout.setContentsMargins(15, 15, 15, 15)
        serial_layout.setSpacing(12)
        
        # 第一行：串口和波特率设置
        port_baud_layout = QHBoxLayout()
        port_baud_layout.setSpacing(10)
        
        # 串口选择
        port_layout = QHBoxLayout()
        port_label = QLabel("串口:")
        port_label.setFont(QFont("Microsoft YaHei", 9))
        port_layout.addWidget(port_label)
        self.combo_port = QComboBox()
        self.combo_port.setMinimumWidth(120)
        self.combo_port.setFont(QFont("Consolas", 9))
        port_layout.addWidget(self.combo_port)
        port_baud_layout.addLayout(port_layout)
        
        # 刷新按钮
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setMaximumWidth(80)
        self.btn_refresh.setFont(QFont("Microsoft YaHei", 9))
        self.btn_refresh.clicked.connect(self.refresh_ports)
        port_baud_layout.addWidget(self.btn_refresh)
        
        # 波特率选择
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
        port_baud_layout.addLayout(baud_layout)
                        
        # 打开/关闭串口按钮
        self.btn_switch = QPushButton("打开串口")
        self.btn_switch.setCheckable(True)
        self.btn_switch.setFont(QFont("Microsoft YaHei", 9))
        self.btn_switch.setMinimumWidth(100)
        self.btn_switch.clicked.connect(self.toggle_serial)
        port_baud_layout.addWidget(self.btn_switch)
        
        # 更多串口设置按钮
        self.btn_more_settings = QPushButton("更多串口设置")
        self.btn_more_settings.setFont(QFont("Microsoft YaHei", 9))
        self.btn_more_settings.setMinimumWidth(100)
        self.btn_more_settings.clicked.connect(self.show_more_settings)
        port_baud_layout.addWidget(self.btn_more_settings)
        
        serial_layout.addLayout(port_baud_layout)
        
        # 第二行：显示和自动保存设置
        display_save_layout = QHBoxLayout()
        display_save_layout.setSpacing(15)
        
        # 接收区设置：HEX显示
        self.check_hex_recv = QCheckBox("HEX显示")
        self.check_hex_recv.setFont(QFont("Microsoft YaHei", 9))
        display_save_layout.addWidget(self.check_hex_recv)
        
        # 显示时间戳复选框
        self.check_timestamp = QCheckBox("显示时间")
        self.check_timestamp.setChecked(True)  # 默认显示时间
        self.check_timestamp.setFont(QFont("Microsoft YaHei", 9))
        display_save_layout.addWidget(self.check_timestamp)
        
        display_save_layout.addStretch()
        
        # 自动保存复选框
        self.check_auto_save = QCheckBox("自动保存日志")
        self.check_auto_save.setFont(QFont("Microsoft YaHei", 9))
        self.check_auto_save.stateChanged.connect(self.toggle_auto_save)
        display_save_layout.addWidget(self.check_auto_save)
        
        serial_layout.addLayout(display_save_layout)
        
        # 第三行：保存路径选择
        path_layout = QHBoxLayout()
        path_layout.setSpacing(10)
        self.label_save_path = QLabel("保存路径:")
        self.label_save_path.setFont(QFont("Microsoft YaHei", 9))
        path_layout.addWidget(self.label_save_path)
        self.line_edit_save_path = QLineEdit()
        self.line_edit_save_path.setReadOnly(True)
        self.line_edit_save_path.setText(self.save_directory)
        self.line_edit_save_path.setMinimumWidth(450)
        self.line_edit_save_path.setFont(QFont("Consolas", 8))
        path_layout.addWidget(self.line_edit_save_path)
        self.btn_browse_path = QPushButton("浏览")
        self.btn_browse_path.setMaximumWidth(80)
        self.btn_browse_path.setFont(QFont("Microsoft YaHei", 9))
        self.btn_browse_path.clicked.connect(self.browse_save_path)
        path_layout.addWidget(self.btn_browse_path)
        
        serial_layout.addLayout(path_layout)
        
        # 第四行：操作按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # 清除接收区按钮
        self.btn_clear_recv = QPushButton("清空接收")
        self.btn_clear_recv.setFont(QFont("Microsoft YaHei", 9))
        self.btn_clear_recv.clicked.connect(self.clear_recv_area)
        button_layout.addWidget(self.btn_clear_recv)
        
        serial_layout.addLayout(button_layout)
        
        main_layout.addWidget(serial_group)

        # --- 中间接收和发送区域（使用分割器）---
        splitter = QSplitter(Qt.Vertical)
        
        # 接收区
        recv_group = QGroupBox("接收区")
        recv_group.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        recv_layout = QVBoxLayout(recv_group)
        recv_layout.setContentsMargins(5, 5, 5, 5)
        
        self.text_recv = QTextEdit()
        self.text_recv.setReadOnly(True)
        self.text_recv.setFont(QFont("Consolas", 10))
        # 设置背景色为白色，文本颜色为黑色
        self.text_recv.setStyleSheet("QTextEdit { background-color: white; color: black; border: 1px solid #CCCCCC; border-radius: 4px; }")
        recv_layout.addWidget(self.text_recv)
        
        # 图示区域
        graph_group = QGroupBox("数据统计")
        graph_group.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        graph_layout = QHBoxLayout(graph_group)
        graph_layout.setContentsMargins(10, 10, 10, 10)
        
        # 接收字节数统计
        self.label_rx_bytes = QLabel("接收字节: 0")
        self.label_rx_bytes.setFont(QFont("Consolas", 8))
        graph_layout.addWidget(self.label_rx_bytes)
        
        # 发送字节数统计
        self.label_tx_bytes = QLabel("发送字节: 0")
        self.label_tx_bytes.setFont(QFont("Consolas", 8))
        graph_layout.addWidget(self.label_tx_bytes)
        
        # 数据包数量统计
        self.label_packets = QLabel("数据包: 0")
        self.label_packets.setFont(QFont("Consolas", 8))
        graph_layout.addWidget(self.label_packets)
        
        graph_layout.addStretch()
        
        recv_layout.addWidget(graph_group)
        splitter.addWidget(recv_group)
        
        # 发送区
        send_group = QGroupBox("发送区")
        send_group.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        send_layout = QVBoxLayout(send_group)
        send_layout.setContentsMargins(15, 15, 15, 15)
        send_layout.setSpacing(10)
        
        # 发送设置行
        send_settings_layout = QHBoxLayout()
        send_settings_layout.setSpacing(15)
        
        # 发送区设置：HEX发送
        self.check_hex_send = QCheckBox("HEX发送")
        self.check_hex_send.setFont(QFont("Microsoft YaHei", 9))
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
        
        # 校验选项
        self.label_checksum = QLabel("校验:")
        self.label_checksum.setFont(QFont("Microsoft YaHei", 9))
        send_settings_layout.addWidget(self.label_checksum)
        self.combo_checksum = QComboBox()
        self.combo_checksum.addItems(["None", "ModbusCRC16", "CRC32", "Fletcher", "XOR8", "ADD8", "ADD16"])
        self.combo_checksum.setFont(QFont("Consolas", 9))
        send_settings_layout.addWidget(self.combo_checksum)

        # 重复发送相关控件（使用子布局，设置更小的间距）
        repeat_layout = QHBoxLayout()
        repeat_layout.setSpacing(5)
        
        # 重复发送选项
        self.check_repeat = QCheckBox("重复发送")
        self.check_repeat.setFont(QFont("Microsoft YaHei", 9))
        repeat_layout.addWidget(self.check_repeat)

        # 重复发送时间设置
        repeat_layout.addWidget(QLabel("间隔(ms):"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(100, 5000)
        self.spin_interval.setValue(1000)
        self.spin_interval.setFont(QFont("Consolas", 9))
        self.spin_interval.setEnabled(False)  # 默认禁用
        repeat_layout.addWidget(self.spin_interval)
        
        send_settings_layout.addLayout(repeat_layout)
        
        send_layout.addLayout(send_settings_layout)
        
        # 首尾字段输入框（平行布局）
        fields_layout = QHBoxLayout()
        
        # 发送首字段
        fields_layout.addWidget(QLabel("发送首字段:"))
        self.text_ota = QLineEdit()
        self.text_ota.setFont(QFont("Consolas", 10))
        self.text_ota.setPlaceholderText("输入首字段...")
        self.text_ota.setStyleSheet("QLineEdit { border: 1px solid #CCCCCC; border-radius: 4px; }")
        fields_layout.addWidget(self.text_ota)
        
        # 发送尾字段
        fields_layout.addWidget(QLabel("发送尾字段:"))
        self.text_tail = QLineEdit()
        self.text_tail.setFont(QFont("Consolas", 10))
        self.text_tail.setPlaceholderText("输入尾字段...")
        self.text_tail.setStyleSheet("QLineEdit { border: 1px solid #CCCCCC; border-radius: 4px; }")
        fields_layout.addWidget(self.text_tail)
        
        send_layout.addLayout(fields_layout)
        
        # 发送输入框
        self.text_send = QTextEdit()
        self.text_send.setMaximumHeight(100)
        self.text_send.setFont(QFont("Consolas", 10))
        self.text_send.setPlaceholderText("在此输入要发送的内容...")
        self.text_send.setStyleSheet("QTextEdit { border: 1px solid #CCCCCC; border-radius: 4px; }")
        send_layout.addWidget(self.text_send)
        
        # 发送按钮行
        send_buttons_layout = QHBoxLayout()
        send_buttons_layout.setSpacing(10)
        
        # 发送按钮
        self.btn_send = QPushButton("发送")
        self.btn_send.setFont(QFont("Microsoft YaHei", 9))
        self.btn_send.setMinimumWidth(80)
        self.btn_send.clicked.connect(self.send_data)
        # 允许通过回车键发送（需要Ctrl+回车，因为TextEdit默认换行是回车）
        self.btn_send.setShortcut("Ctrl+Return") 

        # 停止按钮（用于停止重复发送）
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setFont(QFont("Microsoft YaHei", 9))
        self.btn_stop.setMinimumWidth(80)
        self.btn_stop.clicked.connect(self.stop_repeat)
        self.btn_stop.setEnabled(False)  # 默认禁用

        send_buttons_layout.addWidget(self.btn_send)
        send_buttons_layout.addWidget(self.btn_stop)
        send_buttons_layout.addStretch()
        
        # 保存参数按钮
        self.btn_save_params = QPushButton("保存参数")
        self.btn_save_params.setFont(QFont("Microsoft YaHei", 9))
        self.btn_save_params.setMinimumWidth(80)
        self.btn_save_params.clicked.connect(self.save_config)
        send_buttons_layout.addWidget(self.btn_save_params)
        
        # 添加显示/隐藏多字符发送区域的按钮
        self.btn_toggle_multi_send = QPushButton("显示多字符发送")
        self.btn_toggle_multi_send.setFont(QFont("Microsoft YaHei", 9))
        self.btn_toggle_multi_send.setMinimumWidth(120)
        self.btn_toggle_multi_send.clicked.connect(self.toggle_multi_send)
        send_buttons_layout.addWidget(self.btn_toggle_multi_send)
        
        send_layout.addLayout(send_buttons_layout)
        splitter.addWidget(send_group)
        
        # 设置分割器的初始大小比例，接收区占70%，发送区占30%
        splitter.setSizes([700, 300])
        
        main_layout.addWidget(splitter)
        
        # 添加多字符发送区域（默认隐藏）
        self.multi_send_widget = QWidget()
        self.multi_send_layout = QVBoxLayout(self.multi_send_widget)
        self.multi_send_layout.setContentsMargins(0, 0, 0, 0)
        self.multi_send_layout.setSpacing(10)
        
        # 多字符发送组
        multi_send_group = QGroupBox("多字符串发送")
        multi_send_group.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        multi_send_group_layout = QVBoxLayout(multi_send_group)
        multi_send_group_layout.setContentsMargins(10, 10, 10, 10)
        multi_send_group_layout.setSpacing(8)
        
        # 工具栏
        toolbar_layout = QHBoxLayout()
        
        # 循环发送复选框（用于控制批量发送的开始/停止）
        self.check_cycle_send = QCheckBox("循环发送")
        self.check_cycle_send.setFont(QFont("Microsoft YaHei", 8))
        self.check_cycle_send.stateChanged.connect(self.toggle_batch_send)
        toolbar_layout.addWidget(self.check_cycle_send)
        
        # 延时标签和输入框
        toolbar_layout.addWidget(QLabel("延时:"))
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 10000)
        self.spin_delay.setValue(1000)
        self.spin_delay.setFont(QFont("Consolas", 8))
        self.spin_delay.setMinimumWidth(60)
        toolbar_layout.addWidget(self.spin_delay)
        toolbar_layout.addWidget(QLabel("ms"))
        
        # 添加分隔符
        toolbar_layout.addWidget(QFrame())
        
        # 保存/加载按钮
        btn_save = QPushButton("保存")
        btn_save.setFont(QFont("Microsoft YaHei", 8))
        btn_save.setMinimumWidth(50)
        btn_save.clicked.connect(self.save_multi_items)
        toolbar_layout.addWidget(btn_save)
        
        btn_load = QPushButton("加载")
        btn_load.setFont(QFont("Microsoft YaHei", 8))
        btn_load.setMinimumWidth(50)
        btn_load.clicked.connect(self.load_multi_items)
        toolbar_layout.addWidget(btn_load)
        
        toolbar_layout.addStretch()
        multi_send_group_layout.addLayout(toolbar_layout)
        
        # 多字符列表
        self.table_multi_send = QTableWidget()
        self.table_multi_send.setColumnCount(5)
        self.table_multi_send.setHorizontalHeaderLabels(["HEX", "字符串", "点击发送", "延时(ms)", "顺序"])
        
        # 设置编辑触发模式为单击
        self.table_multi_send.setEditTriggers(QAbstractItemView.CurrentChanged | QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        
        # 设置列宽调整模式
        header = self.table_multi_send.horizontalHeader()
        # 将所有列设置为可调整宽度
        header.setSectionResizeMode(QHeaderView.Interactive)
        
        # 设置初始列宽
        self.table_multi_send.setColumnWidth(0, 40)   # HEX列
        self.table_multi_send.setColumnWidth(1, 250)  # 字符串列
        self.table_multi_send.setColumnWidth(2, 100)  # 点击发送列
        self.table_multi_send.setColumnWidth(3, 80)   # 延时列
        self.table_multi_send.setColumnWidth(4, 60)   # 顺序列
        
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
            
            # 字符串（支持双击编辑按钮内容）
            string_item = QTableWidgetItem(item[1])
            string_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table_multi_send.setItem(i, 1, string_item)
            
            # 发送按钮（支持双击编辑）
            send_btn = QPushButton(item[2])
            send_btn.setFont(QFont("Microsoft YaHei", 8))
            send_btn.setMinimumWidth(70)  # 增加按钮宽度
            send_btn.clicked.connect(lambda checked, row=i: self.send_multi_item(row))
            send_btn.setObjectName(f"btn_{i}")
            # 安装事件过滤器来处理双击事件
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
            delay_spin.setFont(QFont("Consolas", 8))
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
        self.table_multi_send.doubleClicked.connect(self.table_double_click)
        
        multi_send_group_layout.addWidget(self.table_multi_send)
        
        # 添加/删除按钮
        btn_layout = QHBoxLayout()
        btn_add = QPushButton("+")
        btn_add.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        btn_add.setMinimumWidth(30)
        btn_add.clicked.connect(self.add_multi_item)
        btn_layout.addWidget(btn_add)
        
        btn_remove = QPushButton("-")
        btn_remove.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        btn_remove.setMinimumWidth(30)
        btn_remove.clicked.connect(self.remove_multi_item)
        btn_layout.addWidget(btn_remove)
        
        btn_layout.addStretch()
        multi_send_group_layout.addLayout(btn_layout)
        
        self.multi_send_layout.addWidget(multi_send_group)
        
        # 创建一个垂直分割器，用于在右侧放置多字符发送区域
        self.main_splitter = QSplitter(Qt.Horizontal)
        
        # 创建左侧内容
        left_content = QWidget()
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_layout.addWidget(serial_group)
        left_layout.addWidget(splitter)
        
        # 创建右侧内容（多字符发送区域）
        right_content = QWidget()
        right_layout = QVBoxLayout(right_content)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(self.multi_send_widget)
        
        # 添加到分割器
        self.main_splitter.addWidget(left_content)
        self.main_splitter.addWidget(right_content)
        
        # 设置分割器大小，左侧占80%，右侧占20%
        self.main_splitter.setSizes([800, 200])
        
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
        self.statusBar().showMessage("就绪")
        self.statusBar().setStyleSheet("QStatusBar { background-color: #F0F0F0; border-top: 1px solid #CCCCCC; }")
        
        # 添加状态栏组件
        self.status_connection = QLabel("连接状态: 未连接")
        self.status_connection.setFont(QFont("Microsoft YaHei", 9))
        self.status_baud = QLabel("波特率: 115200")
        self.status_baud.setFont(QFont("Microsoft YaHei", 9))
        self.status_log = QLabel("日志文件: 未创建")
        self.status_log.setFont(QFont("Microsoft YaHei", 9))
        
        self.statusBar().addPermanentWidget(self.status_connection)
        self.statusBar().addPermanentWidget(QLabel(" | "))
        self.statusBar().addPermanentWidget(self.status_baud)
        self.statusBar().addPermanentWidget(QLabel(" | "))
        self.statusBar().addPermanentWidget(self.status_log)

    def toggle_repeat(self):
        """切换重复发送状态"""
        if self.check_repeat.isChecked():
            self.spin_interval.setEnabled(True)
        else:
            self.spin_interval.setEnabled(False)
            self.stop_repeat()

    def stop_repeat(self):
        """停止重复发送"""
        if self.repeat_timer.isActive():
            self.repeat_timer.stop()
            self.append_text("[系统]: 停止重复发送\n")
        self.btn_stop.setEnabled(False)
        self.check_repeat.setChecked(False)

    def handle_baud_change(self, index):
        """处理波特率选择变化"""
        if self.combo_baud.itemText(index) == "自定义":
            # 弹出输入对话框，让用户输入波特率
            from PyQt5.QtWidgets import QInputDialog
            baud_rate, ok = QInputDialog.getInt(self, "自定义波特率", "请输入波特率:", 115200, 1, 1000000)
            if ok:
                # 替换"自定义"选项为用户输入的波特率
                self.combo_baud.setItemText(index, str(baud_rate))
                # 设置当前选项为用户输入的波特率
                self.combo_baud.setCurrentIndex(index)
            else:
                # 如果用户取消输入，恢复到之前的波特率
                self.combo_baud.setCurrentText('115200')

    # --- 功能函数 ---

    def refresh_ports(self):
        """刷新可用的串口列表"""
        self.combo_port.clear()
        ports = serial.tools.list_ports.comports()
        if ports:
            for port in ports:
                self.combo_port.addItem(port.device)
            self.combo_port.setCurrentIndex(0) # 默认选中第一个
        else:
            self.combo_port.addItem("无可用串口")

    def toggle_serial(self):
        """打开或关闭串口"""
        if self.btn_switch.isChecked():
            # 尝试打开串口
            port_name = self.combo_port.currentText()
            if port_name == "无可用串口":
                QMessageBox.warning(self, "错误", "没有检测到可用串口！")
                self.btn_switch.setChecked(False)
                return

            try:
                baud_rate = int(self.combo_baud.currentText())
            except ValueError:
                QMessageBox.warning(self, "错误", "无效的波特率！")
                self.btn_switch.setChecked(False)
                return

            try:
                # 关闭之前可能存在的串口连接
                if hasattr(self, 'serial_port') and self.serial_port:
                    try:
                        if self.serial_port.is_open:
                            self.serial_port.close()
                    except Exception as e:
                        self.append_text(f"[错误]: 关闭旧串口失败: {str(e)}\n")
                
                # 获取串口设置参数
                # 数据位
                try:
                    data_bits = int(self.serial_data_bits) if hasattr(self, 'serial_data_bits') else 8
                except (AttributeError, ValueError):
                    data_bits = 8
                bytesize_map = {
                    5: serial.FIVEBITS,
                    6: serial.SIXBITS,
                    7: serial.SEVENBITS,
                    8: serial.EIGHTBITS
                }
                bytesize = bytesize_map.get(data_bits, serial.EIGHTBITS)
                
                # 停止位
                try:
                    stop_bits = self.serial_stop_bits if hasattr(self, 'serial_stop_bits') else '1'
                except AttributeError:
                    stop_bits = '1'
                stopbits_map = {
                    '1': serial.STOPBITS_ONE,
                    '1.5': serial.STOPBITS_ONE_POINT_FIVE,
                    '2': serial.STOPBITS_TWO
                }
                stopbits = stopbits_map.get(stop_bits, serial.STOPBITS_ONE)
                
                # 校验位
                try:
                    parity = self.serial_parity if hasattr(self, 'serial_parity') else 'None'
                except AttributeError:
                    parity = 'None'
                parity_map = {
                    'None': serial.PARITY_NONE,
                    'Even': serial.PARITY_EVEN,
                    'Odd': serial.PARITY_ODD,
                    'Mark': serial.PARITY_MARK,
                    'Space': serial.PARITY_SPACE
                }
                parity = parity_map.get(parity, serial.PARITY_NONE)
                
                # 流控制
                try:
                    flow_control = self.serial_flow_control if hasattr(self, 'serial_flow_control') else 'None'
                except AttributeError:
                    flow_control = 'None'
                
                # 处理不同版本pyserial的流控制常量名称差异
                try:
                    # 较新版本的pyserial
                    flowcontrol_map = {
                        'None': serial.FLOWCONTROL_NONE,
                        'Xon/Xoff': serial.FLOWCONTROL_XONXOFF,
                        'RTS/CTS': serial.FLOWCONTROL_RTSCTS,
                        'DSR/DTR': serial.FLOWCONTROL_DSRDTR
                    }
                    flowcontrol = flowcontrol_map.get(flow_control, serial.FLOWCONTROL_NONE)
                except AttributeError:
                    # 较旧版本的pyserial
                    flowcontrol_map = {
                        'None': 0,
                        'Xon/Xoff': 1,
                        'RTS/CTS': 2,
                        'DSR/DTR': 3
                    }
                    flowcontrol = flowcontrol_map.get(flow_control, 0)
                
                # 初始化串口对象
                # 移除flowcontrol参数，使用默认设置（无流控制）
                self.serial_port = serial.Serial(
                    port=port_name,
                    baudrate=baud_rate,
                    bytesize=bytesize,
                    parity=parity,
                    stopbits=stopbits,
                    timeout=0.1
                )
                
                # 设置RTS和DTR的初始状态
                try:
                    self.serial_port.rts = self.check_rts.isChecked()
                    self.serial_port.dtr = self.check_dtr.isChecked()
                except Exception as e:
                    self.append_text(f"[警告]: 设置RTS/DTR状态失败: {str(e)}\n")
                
                # 启动接收线程
                self.read_thread = SerialReadThread(self.serial_port)
                self.read_thread.receive_data_signal.connect(self.handle_receive_data)
                self.read_thread.error_signal.connect(self.handle_read_error)
                self.read_thread.start()

                # 更新UI状态
                self.btn_switch.setText("关闭串口")
                self.combo_port.setEnabled(False)
                self.combo_baud.setEnabled(False)
                self.btn_refresh.setEnabled(False)
                self.append_text(f"--- 串口 {port_name} 已打开, 波特率 {baud_rate} ---")
                
                # 更新状态栏
                self.status_connection.setText("连接状态: 已连接")
                self.status_baud.setText(f"波特率: {baud_rate}")

            except Exception as e:
                error_msg = f"打开串口失败: {str(e)}"
                QMessageBox.critical(self, "串口打开失败", error_msg)
                self.btn_switch.setChecked(False)
                self.append_text(f"[错误]: {error_msg}\n")
                # 确保串口被关闭
                if hasattr(self, 'serial_port') and self.serial_port:
                    try:
                        if self.serial_port.is_open:
                            self.serial_port.close()
                    except:
                        pass
        else:
            # 关闭串口
            # 停止批量发送
            if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
                self.batch_thread.stop()
                self.check_cycle_send.setChecked(False)
                self.append_text("[系统]: 批量发送已停止\n")
            
            # 停止接收线程
            if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
                try:
                    self.read_thread.stop()
                except Exception as e:
                    self.append_text(f"[错误]: 停止接收线程失败: {str(e)}\n")
            
            # 关闭串口
            if hasattr(self, 'serial_port') and self.serial_port:
                try:
                    if self.serial_port.is_open:
                        self.serial_port.close()
                        self.append_text(f"--- 串口已关闭 ---")
                except Exception as e:
                    self.append_text(f"[错误]: 关闭串口失败: {str(e)}\n")

            # 更新状态栏
            self.status_connection.setText("连接状态：未连接")
            self.status_baud.setText("波特率：115200")
            
            # 恢复 UI 状态
            self.btn_switch.setText("打开串口")
            self.combo_port.setEnabled(True)
            self.combo_baud.setEnabled(True)
            self.btn_refresh.setEnabled(True)

    def send_data(self):
        """发送数据"""
        # 检查串口状态
        if not hasattr(self, 'serial_port') or not self.serial_port or not self.serial_port.is_open:
            QMessageBox.warning(self, "警告", "请先打开串口！")
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
                # 如果有首字段，添加到数据前面
                if head_field:
                    head_data = head_field.encode('utf-8')
                    data = head_data + data
                # 如果有尾字段，添加到数据后面
                if tail_field:
                    tail_data = tail_field.encode('utf-8')
                    data = data + tail_data
                # HEX模式下，回车换行需要手动添加十六进制的 \r\n
            else:
                # 文本发送模式
                # 如果有首字段，添加到内容前面
                if head_field:
                    content = head_field + content
                # 如果有尾字段，添加到内容后面
                if tail_field:
                    content = content + tail_field
                # 处理回车换行
                if self.check_newline.isChecked():
                    content = content.rstrip('\r\n')  # 去除末尾的换行符，避免重复添加
                    data = (content + '\r\n').encode('utf-8')
                else:
                    data = content.encode('utf-8')

            # 计算并添加校验值
            checksum_type = self.combo_checksum.currentText()
            if checksum_type != "None":
                checksum = self.calculate_checksum(data, checksum_type)
                if checksum:
                    # 添加校验值到数据末尾
                    data_with_checksum = data + checksum
                    # 发送带校验值的数据
                    bytes_sent = self.serial_port.write(data_with_checksum)
                    # 更新发送数据统计
                    self.tx_bytes += bytes_sent
                    self.label_tx_bytes.setText(f"发送字节: {self.tx_bytes}")
                    # 显示发送的内容和校验值
                    checksum_hex = ' '.join([f'{byte:02X}' for byte in checksum])
                    self.append_text(f"[发送]: {content}\n")
                    self.append_text(f"[校验]: {checksum_type} = {checksum_hex}\n")
                    self.append_text(f"[系统]: 已发送 {bytes_sent} 字节（含校验值）\n")
            else:
                # 发送原始数据
                bytes_sent = self.serial_port.write(data)
                # 更新发送数据统计
                self.tx_bytes += bytes_sent
                self.label_tx_bytes.setText(f"发送字节: {self.tx_bytes}")
                # 在接收区显示发送的内容，让用户能看到发送状态
                self.append_text(f"[发送]: {content}\n")
                self.append_text(f"[系统]: 已发送 {bytes_sent} 字节\n")
            
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
                
        except Exception as e:
            error_msg = f"发送失败: {str(e)}"
            QMessageBox.critical(self, "发送失败", error_msg)
            self.append_text(f"[错误]: {error_msg}\n")

    def process_ansi_colors(self, text):
        """处理ANSI颜色转义序列，返回文本和格式信息"""
        import re
        # ANSI颜色代码映射到QColor
        ansi_colors = {
            '30': QColor(0, 0, 0),      # 黑色
            '31': QColor(255, 0, 0),    # 红色
            '32': QColor(0, 128, 0),    # 深绿色（更清晰）
            '33': QColor(165, 42, 42),  # 棕色（更清晰）
            '34': QColor(0, 0, 255),    # 蓝色
            '35': QColor(128, 0, 128),  # 深紫色（更清晰）
            '36': QColor(0, 128, 128),  # 深青色（更清晰）
            '37': QColor(128, 128, 128), # 灰色（更清晰）
            '90': QColor(128, 128, 128), # 亮黑（灰色）
            '91': QColor(255, 0, 0),    # 亮红
            '92': QColor(0, 128, 0),    # 亮绿（更清晰）
            '93': QColor(165, 42, 42),  # 亮黄（更清晰）
            '94': QColor(0, 0, 255),    # 亮蓝
            '95': QColor(128, 0, 128),  # 亮紫（更清晰）
            '96': QColor(0, 128, 128),  # 亮青（更清晰）
            '97': QColor(128, 128, 128), # 亮白（更清晰）
        }
        
        # ANSI背景颜色代码映射到QColor
        ansi_bg_colors = {
            '40': QColor(0, 0, 0),      # 黑色背景
            '41': QColor(255, 0, 0),    # 红色背景
            '42': QColor(0, 255, 0),    # 绿色背景
            '43': QColor(255, 255, 0),  # 黄色背景
            '44': QColor(0, 0, 255),    # 蓝色背景
            '45': QColor(255, 0, 255),  # 紫色背景
            '46': QColor(0, 255, 255),  # 青色背景
            '47': QColor(255, 255, 255), # 白色背景
        }
        
        # 处理ANSI转义序列
        result = []
        current_format = QTextCharFormat()
        current_format.setForeground(QColor(0, 0, 0))  # 默认黑色，与白色背景对比清晰
        
        # 使用正则表达式匹配ANSI转义序列和单独的\x1B字符
        ansi_pattern = re.compile(r'\x1B(?:\[([0-9;]*)m)?')
        
        last_end = 0
        for match in ansi_pattern.finditer(text):
            # 添加匹配前的文本
            plain_text = text[last_end:match.start()]
            if plain_text:
                # 处理控制字符
                processed_plain = ''
                for char in plain_text:
                    if ord(char) < 32 and char not in '\r\n\t':
                        # 对于其他控制字符，显示为 \xXX 形式
                        processed_plain += f'\\x{ord(char):02X}'
                    else:
                        processed_plain += char
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
                        current_format.setForeground(QColor(0, 0, 0))  # 重置为黑色前景色，确保在白色背景下可见
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
            processed_remaining = ''
            for char in remaining_text:
                if ord(char) < 32 and char not in '\r\n\t':
                    processed_remaining += f'\\x{ord(char):02X}'
                else:
                    processed_remaining += char
            result.append((processed_remaining, QTextCharFormat(current_format)))
        
        return result

    def handle_read_error(self, error_msg):
        """处理串口读取错误"""
        # 显示错误信息
        QMessageBox.critical(self, "读取错误", error_msg)
        self.append_text(f"[错误]: {error_msg}\n")
        
        # 停止批量发送
        if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
            self.batch_thread.stop()
            self.check_cycle_send.setChecked(False)
            self.append_text("[系统]: 批量发送已停止\n")
        
        # 停止接收线程
        if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
            try:
                self.read_thread.stop()
            except Exception as e:
                self.append_text(f"[错误]: 停止接收线程失败: {str(e)}\n")
        
        # 关闭串口
        if hasattr(self, 'serial_port') and self.serial_port:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except Exception as e:
                self.append_text(f"[错误]: 关闭串口失败: {str(e)}\n")
        
        # 更新UI状态
        self.btn_switch.setText("打开串口")
        self.btn_switch.setChecked(False)
        self.combo_port.setEnabled(True)
        self.combo_baud.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        
        # 更新状态栏
        self.status_connection.setText("连接状态: 未连接")
        self.status_baud.setText("波特率: 115200")
        self.statusBar().showMessage(f"串口读取错误: {error_msg}")

    def handle_receive_data(self, data):
        """处理接收到的数据"""
        try:
            # 更新接收数据统计
            self.rx_bytes += len(data)
            self.packets += 1
            self.label_rx_bytes.setText(f"接收字节: {self.rx_bytes}")
            self.label_packets.setText(f"数据包: {self.packets}")
            
            # 获取当前时间戳
            import datetime
            timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")
            
            if self.check_hex_recv.isChecked():
                # HEX 显示
                hex_str = ' '.join([f'{byte:02X}' for byte in data])
                # 添加到日志缓冲区
                log_entry = timestamp + hex_str
                self.log_buffer.append(log_entry)
                # 显示到界面
                if self.check_timestamp.isChecked():
                    # 先添加时间戳，在新的一行
                    cursor = self.text_recv.textCursor()
                    cursor.movePosition(QTextCursor.End)
                    cursor.insertText('\n' + timestamp)
                    # 再添加HEX文本，不添加额外的换行符
                    cursor.insertText(hex_str + '\n')
                else:
                    self.append_text(hex_str)
                # 自动保存
                if self.auto_save_enabled and self.current_log_file:
                    self.auto_save_data(log_entry + '\n')
            else:
                # 文本显示，处理特殊字符和转义序列
                text = data.decode('utf-8', errors='replace')
                # 添加到日志缓冲区
                log_entry = timestamp + text
                self.log_buffer.append(log_entry)
                # 处理ANSI颜色转义序列和控制字符
                formatted_segments = self.process_ansi_colors(text)
                # 显示到界面
                if self.check_timestamp.isChecked():
                    # 先添加时间戳，在新的一行
                    cursor = self.text_recv.textCursor()
                    cursor.movePosition(QTextCursor.End)
                    cursor.insertText('\n' + timestamp)
                # 再添加格式化文本
                self.append_formatted_text(formatted_segments)
                # 自动保存
                if self.auto_save_enabled and self.current_log_file:
                    self.auto_save_data(log_entry + '\n')
        except Exception as e:
            error_msg = f"[解码错误]: {e}"
            # 添加到日志缓冲区
            import datetime
            timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")
            log_entry = timestamp + error_msg
            self.log_buffer.append(log_entry)
            # 显示到界面
            self.append_text(f"\n{error_msg}\n")
            # 自动保存
            if self.auto_save_enabled and self.current_log_file:
                self.auto_save_data(log_entry + '\n')

    def append_formatted_text(self, formatted_segments):
        """向接收区追加带格式的文本，并自动滚动到底部"""
        # 开始一个编辑块，提高性能
        cursor = self.text_recv.textCursor()
        cursor.beginEditBlock()
        
        # 移动到文本末尾
        cursor.movePosition(QTextCursor.End)
        
        # 添加带格式的文本段
        for text, format in formatted_segments:
            cursor.movePosition(QTextCursor.End)
            cursor.setCharFormat(format)
            cursor.insertText(text)
        
        # 结束编辑块
        cursor.endEditBlock()
        
        # 确保文本可见
        self.text_recv.ensureCursorVisible()
        
        # 滚动到底部
        self.text_recv.verticalScrollBar().setValue(self.text_recv.verticalScrollBar().maximum())

    def append_text(self, text):
        """向接收区追加文本，并自动滚动到底部"""
        # 直接使用文本，不添加时间戳（时间戳已经在调用前添加）
        display_text = text
        
        # 只有系统消息、错误消息和发送消息才添加到日志缓冲区
        if "[系统]:" in text or "[错误]:" in text or "[发送]:" in text:
            import datetime
            timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")
            self.log_buffer.append(timestamp + text)
        
        # 获取光标
        cursor = self.text_recv.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        # 根据消息类型设置不同的颜色
        if "[发送]:" in text:
            # 发送的内容使用蓝色
            format = QTextCharFormat()
            format.setForeground(QColor(0, 0, 255))  # 蓝色
            cursor.setCharFormat(format)
        elif "[系统]:" in text:
            # 系统消息使用灰色
            format = QTextCharFormat()
            format.setForeground(QColor(128, 128, 128))  # 灰色
            cursor.setCharFormat(format)
        elif "[错误]:" in text:
            # 错误消息使用红色
            format = QTextCharFormat()
            format.setForeground(QColor(255, 0, 0))  # 红色
            cursor.setCharFormat(format)
        else:
            # 其他消息使用黑色
            format = QTextCharFormat()
            format.setForeground(QColor(0, 0, 0))  # 黑色
            cursor.setCharFormat(format)
        
        # 插入文本
        cursor.insertText(display_text + '\n')
        
        # 确保文本可见
        self.text_recv.ensureCursorVisible()
        
        # 滚动到底部
        self.text_recv.verticalScrollBar().setValue(self.text_recv.verticalScrollBar().maximum())

    def clear_recv_area(self):
        """清空接收区"""
        self.text_recv.clear()
        # 清空日志缓冲区
        self.log_buffer.clear()
        # 添加系统消息
        self.append_text("[系统]: 接收区已清空")

    def browse_save_path(self):
        """浏览保存路径"""
        from PyQt5.QtWidgets import QFileDialog
        import os
        
        # 弹出目录选择对话框
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", self.save_directory)
        
        if directory:
            self.save_directory = directory
            self.line_edit_save_path.setText(self.save_directory)
            self.append_text(f"[系统]: 保存路径已设置为: {self.save_directory}\n")
            # 保存配置
            self.save_config()

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
                    # 强制写入磁盘
                    import os
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
            
            self.append_text("[系统]: 自动保存功能已开启\n")
        else:  # 取消勾选
            self.auto_save_enabled = False
            # 关闭当前日志文件
            if self.current_log_file:
                try:
                    self.current_log_file.close()
                    self.current_log_file = None
                    self.append_text("[系统]: 日志文件已关闭\n")
                except Exception as e:
                    self.append_text(f"[错误]: 关闭日志文件失败: {str(e)}\n")
            self.append_text("[系统]: 自动保存功能已关闭\n")
        
        # 保存配置
        self.save_config()


    
    def update_rts_dtr(self):
        """更新RTS和DTR状态"""
        if self.serial_port and self.serial_port.is_open:
            try:
                # 设置RTS状态
                self.serial_port.rts = self.check_rts.isChecked()
                # 设置DTR状态
                self.serial_port.dtr = self.check_dtr.isChecked()
            except Exception as e:
                error_msg = f"设置RTS/DTR失败: {str(e)}"
                self.append_text(f"[错误]: {error_msg}\n")
    
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
    
    @pyqtSlot(int)
    def send_multi_item(self, row):
        """发送多字符列表中的项目"""
        # 检查串口状态（静默检查，不显示提示框）
        if not hasattr(self, 'serial_port') or not self.serial_port or not self.serial_port.is_open:
            return
        
        # 获取HEX复选框状态
        hex_widget = self.table_multi_send.cellWidget(row, 0)
        hex_layout = hex_widget.layout()
        hex_checkbox = hex_layout.itemAt(0).widget()
        is_hex = hex_checkbox.isChecked()
        
        # 获取字符串
        string_item = self.table_multi_send.item(row, 1)
        if not string_item:
            return
        
        data = string_item.text()
        if not data:
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
        
        # 发送按钮（支持双击编辑）
        send_btn = QPushButton("无注释")
        send_btn.setFont(QFont("Microsoft YaHei", 8))
        send_btn.setMinimumWidth(70)  # 增加按钮宽度
        send_btn.clicked.connect(lambda checked, r=row: self.send_multi_item(r))
        send_btn.setObjectName(f"btn_{row}")
        # 安装事件过滤器来处理双击事件
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
        delay_spin.setFont(QFont("Consolas", 8))
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
    
    def toggle_multi_send(self):
        """切换多字符发送区域的显示/隐藏状态"""
        right_content = self.main_splitter.widget(1)
        if right_content.isVisible():
            right_content.hide()
            self.btn_toggle_multi_send.setText("显示多字符发送")
            # 调整左侧大小
            self.main_splitter.setSizes([1000, 0])
        else:
            right_content.show()
            self.btn_toggle_multi_send.setText("隐藏多字符发送")
            # 恢复分割器大小
            self.main_splitter.setSizes([800, 200])
    
    @pyqtSlot()
    def stop_batch_send(self):
        """停止批量发送（从线程调用）"""
        if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
            self.batch_thread.stop()
            # 使用QTimer延迟设置，避免信号循环
            QTimer.singleShot(0, lambda: self.check_cycle_send.setChecked(False))
            self.append_text("[系统]: 串口已关闭，批量发送已停止\n")
    
    def toggle_batch_send(self, state):
        """切换批量发送状态"""
        if state == 2:  # 勾选，开始发送
            # 检查是否已经有批量发送线程在运行
            if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
                # 线程正在运行，直接返回
                return
            
            # 清除可能存在的旧batch_thread属性
            if hasattr(self, 'batch_thread'):
                del self.batch_thread
            
            # 检查串口是否打开
            if not hasattr(self, 'serial_port') or not self.serial_port or not self.serial_port.is_open:
                QMessageBox.warning(self, "警告", "请先打开串口！")
                # 使用QTimer延迟设置，避免信号循环
                QTimer.singleShot(0, lambda: self.check_cycle_send.setChecked(False))
                return
            
            # 启动批量发送
            self.batch_send()
        else:  # 取消勾选，停止发送
            # 停止批量发送
            if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
                self.batch_thread.stop()
                self.append_text("[系统]: 批量发送已停止\n")
            # 清除batch_thread属性
            if hasattr(self, 'batch_thread'):
                del self.batch_thread
    
    def batch_send(self):
        """批量发送多字符项目"""
        count = self.table_multi_send.rowCount()
        if count == 0:
            QMessageBox.information(self, "提示", "没有可发送的项目")
            self.check_cycle_send.setChecked(False)
            return
        
        # 获取当前设置
        delay = self.spin_delay.value()
        
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
                valid_items.append((order, i))
            except ValueError:
                continue
        
        # 按顺序排序
        valid_items.sort(key=lambda x: x[0])
        sorted_indices = [index for _, index in valid_items]
        
        if not sorted_indices:
            QMessageBox.information(self, "提示", "没有有效的发送项目（顺序必须大于0）")
            self.check_cycle_send.setChecked(False)
            return
        
        # 开始批量发送
        self.append_text(f"[系统]: 开始批量发送 {len(sorted_indices)} 个项目\n")
        
        # 创建一个线程来处理批量发送
        class BatchSendThread(QThread):
            def __init__(self, parent, sorted_indices, delay):
                super().__init__(parent)
                self.parent = parent
                self.sorted_indices = sorted_indices
                self.delay = delay
                self.running = True
            
            def run(self):
                i = 0
                while self.running:
                    try:
                        # 检查父对象是否仍然存在
                        if not self.parent or not hasattr(self.parent, 'serial_port'):
                            self.running = False
                            break
                        
                        # 发送当前项目（在主线程中执行）
                        # 发送操作本身会检查串口状态
                        QMetaObject.invokeMethod(self.parent, "send_multi_item", Qt.QueuedConnection,
                                                Q_ARG(int, self.sorted_indices[i]))
                        
                        # 添加延时
                        if self.delay > 0:
                            QThread.msleep(self.delay)
                        else:
                            # 即使没有延时，也添加一个小的休眠，避免CPU占用过高
                            QThread.msleep(10)
                        
                        # 循环发送，发送完最后一个项目后重新开始
                        i = (i + 1) % len(self.sorted_indices)
                    except Exception as e:
                        # 捕获所有异常，确保线程能够正常停止
                        print(f"批量发送线程错误: {e}")
                        self.running = False
                        QMetaObject.invokeMethod(self.parent, "stop_batch_send", Qt.QueuedConnection)
                        break
            
            def stop(self):
                self.running = False
        
        # 启动批量发送线程
        self.batch_thread = BatchSendThread(self, sorted_indices, delay)
        self.batch_thread.finished.connect(lambda: self.append_text("[系统]: 批量发送完成\n"))
        self.batch_thread.start()
    

    
    def save_multi_items(self):
        """保存多字符项目到文件"""
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self, "保存多字符项目", "", "JSON Files (*.json);;Text Files (*.txt)"
            )
            if not file_path:
                return
            
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
                
                items.append({"hex": is_hex, "text": text, "delay": delay})
            
            # 保存到文件
            with open(file_path, 'w', encoding='utf-8') as f:
                if file_path.endswith('.json'):
                    import json
                    json.dump(items, f, ensure_ascii=False, indent=2)
                else:
                    for item in items:
                        f.write(f"{item['hex']},{item['text']},{item['delay']}\n")
            self.append_text(f"[系统]: 多字符项目已保存到 {file_path}\n")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存多字符项目失败: {e}")
            self.append_text(f"[错误]: 保存多字符项目失败: {e}\n")
    
    def eventFilter(self, obj, event):
        """事件过滤器，处理双击编辑按钮文本和字符串输入框"""
        if event.type() == event.MouseButtonDblClick:
            # 检查是否是发送按钮
            if obj.objectName().startswith("btn_"):
                # 找出按钮所在的行
                for row in range(self.table_multi_send.rowCount()):
                    widget = self.table_multi_send.cellWidget(row, 2)
                    if widget:
                        layout = widget.layout()
                        if layout and layout.itemAt(0).widget() == obj:
                            # 打开编辑对话框
                            new_text, ok = QInputDialog.getText(
                                self, "编辑按钮文本", "请输入新的按钮文本:", 
                                text=obj.text()
                            )
                            if ok and new_text:
                                obj.setText(new_text)
                            break
        return super().eventFilter(obj, event)
    
    def table_double_click(self, index):
        """处理表格双击事件，双击字符串列时编辑对应按钮的内容"""
        if index.column() == 1:  # 字符串列
            row = index.row()
            # 找出对应行的按钮
            widget = self.table_multi_send.cellWidget(row, 2)
            if widget:
                layout = widget.layout()
                if layout:
                    send_btn = layout.itemAt(0).widget()
                    if send_btn:
                        # 打开编辑对话框
                        new_text, ok = QInputDialog.getText(
                            self, "编辑按钮文本", "请输入新的按钮文本:", 
                            text=send_btn.text()
                        )
                        if ok and new_text:
                            send_btn.setText(new_text)
    

    
    def load_multi_items(self):
        """从文件加载多字符项目"""
        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "加载多字符项目", "", "JSON Files (*.json);;Text Files (*.txt)"
            )
            if not file_path:
                return
            
            items = []
            with open(file_path, 'r', encoding='utf-8') as f:
                if file_path.endswith('.json'):
                    import json
                    items = json.load(f)
                else:
                    for line in f:
                        line = line.strip()
                        if line:
                            parts = line.split(',', 2)
                            if len(parts) == 3:
                                try:
                                    items.append({
                                        "hex": parts[0].lower() == 'true',
                                        "text": parts[1],
                                        "delay": int(parts[2])
                                    })
                                except ValueError:
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
                self.table_multi_send.setItem(i, 1, QTableWidgetItem(item.get("text", "")))
                
                # 发送按钮（支持双击编辑）
                send_btn = QPushButton("无注释")
                send_btn.setFont(QFont("Microsoft YaHei", 8))
                send_btn.setMinimumWidth(50)
                send_btn.clicked.connect(lambda checked, row=i: self.send_multi_item(row))
                send_btn.setObjectName(f"btn_{i}")
                # 安装事件过滤器来处理双击事件
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
                delay_spin.setFont(QFont("Consolas", 8))
                delay_widget = QWidget()
                delay_layout = QHBoxLayout(delay_widget)
                delay_layout.addWidget(delay_spin)
                delay_layout.setAlignment(Qt.AlignCenter)
                delay_layout.setContentsMargins(0, 0, 0, 0)
                self.table_multi_send.setCellWidget(i, 3, delay_widget)
            
            self.append_text(f"[系统]: 从 {file_path} 加载了 {len(items)} 个多字符项目\n")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载多字符项目失败: {e}")
            self.append_text(f"[错误]: 加载多字符项目失败: {e}\n")
    
    def show_more_settings(self):
        """显示更多串口设置"""
        try:
            # 创建设置对话框
            dialog = QDialog(self)
            dialog.setWindowTitle("Setup")
            dialog.setMinimumSize(300, 250)
            
            layout = QVBoxLayout(dialog)
            
            # 设置组
            settings_group = QGroupBox("Settings")
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
            settings_layout.addRow("Port", combo_port_setup)
            
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
            settings_layout.addRow("Baud rate", combo_baud_setup)
            
            # 数据位选择
            combo_data_bits = QComboBox()
            combo_data_bits.addItems(['5', '6', '7', '8'])
            combo_data_bits.setCurrentText('8')
            settings_layout.addRow("Data bits", combo_data_bits)
            
            # 停止位选择
            combo_stop_bits = QComboBox()
            combo_stop_bits.addItems(['1', '1.5', '2'])
            combo_stop_bits.setCurrentText('1')
            settings_layout.addRow("Stop bits", combo_stop_bits)
            
            # 校验位选择
            combo_parity = QComboBox()
            combo_parity.addItems(['None', 'Even', 'Odd', 'Mark', 'Space'])
            combo_parity.setCurrentText('None')
            settings_layout.addRow("Parity", combo_parity)
            
            # 流控制选择
            combo_flow_control = QComboBox()
            combo_flow_control.addItems(['None', 'Xon/Xoff', 'RTS/CTS', 'DSR/DTR'])
            combo_flow_control.setCurrentText('None')
            settings_layout.addRow("Flow control", combo_flow_control)
            
            layout.addWidget(settings_group)
            
            # 按钮布局
            button_layout = QHBoxLayout()
            button_layout.addStretch()
            
            # 确定按钮
            btn_ok = QPushButton("OK")
            btn_ok.clicked.connect(dialog.accept)
            button_layout.addWidget(btn_ok)
            
            # 取消按钮
            btn_cancel = QPushButton("Cancel")
            btn_cancel.clicked.connect(dialog.reject)
            button_layout.addWidget(btn_cancel)
            
            layout.addLayout(button_layout)
            
            # 显示对话框
            if dialog.exec_() == QDialog.Accepted:
                # 应用设置
                selected_port = combo_port_setup.currentText()
                selected_baud = combo_baud_setup.currentText()
                
                # 保存设置的值到实例变量
                self.serial_data_bits = combo_data_bits.currentText()
                self.serial_stop_bits = combo_stop_bits.currentText()
                self.serial_parity = combo_parity.currentText()
                self.serial_flow_control = combo_flow_control.currentText()
                
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
                    # 如果波特率不在列表中，添加到"自定义"选项
                    self.combo_baud.setCurrentText('自定义')
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
            
            # 不再删除旧的日志文件，只创建新文件
            # worker = FileOperationWorker(self.rollover_log_files)
            # self.thread_pool.start(worker)
            
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
            self.statusBar().showMessage(f"已创建新的日志文件: {os.path.basename(self.log_file_path)}")

        except Exception as e:
            error_msg = f"创建日志文件失败: {str(e)}"
            self.append_text(f"[错误]: {error_msg}\n")
            self.current_log_file = None
            self.log_file_path = ""
            self.log_file_size = 0
            # 更新状态栏
            self.status_log.setText("日志文件: 创建失败")
            self.statusBar().showMessage(error_msg)

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
            
            # 计算数据大小（字节）
            data_size = len(data.encode('utf-8'))
            
            # 检查文件大小是否超过限制
            if self.log_file_size + data_size > self.max_log_file_size:
                # 创建新文件
                self.create_new_log_file()
                if not self.current_log_file:
                    return
            
            # 写入数据
            try:
                self.current_log_file.write(data)
                self.current_log_file.flush()  # 立即刷新到磁盘
                # 强制写入磁盘
                import os
                os.fsync(self.current_log_file.fileno())
                self.log_file_size += data_size
                

            except IOError as e:
                # 处理I/O错误
                error_msg = f"写入文件失败: {str(e)}"
                self.append_text(f"[错误]: {error_msg}\n")
                # 尝试重新创建文件
                self.create_new_log_file()
            except Exception as e:
                # 处理其他错误
                error_msg = f"自动保存失败: {str(e)}"
                self.append_text(f"[错误]: {error_msg}\n")
                # 尝试重新创建文件
                self.create_new_log_file()
        except Exception as e:
            # 处理外层错误
            error_msg = f"自动保存过程出错: {str(e)}"
            self.append_text(f"[错误]: {error_msg}\n")
            # 更新状态栏
            self.statusBar().showMessage(error_msg)



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
                    config = json.load(f)
                
                # 加载串口设置
                if 'port' in config:
                    port_index = self.combo_port.findText(config['port'])
                    if port_index >= 0:
                        self.combo_port.setCurrentIndex(port_index)
                
                if 'baudrate' in config:
                    baud_index = self.combo_baud.findText(str(config['baudrate']))
                    if baud_index >= 0:
                        self.combo_baud.setCurrentIndex(baud_index)
                
                # 加载自动保存设置
                if 'auto_save' in config:
                    self.check_auto_save.setChecked(config['auto_save'])
                
                # 加载保存路径
                if 'save_directory' in config:
                    self.save_directory = config['save_directory']
                    self.line_edit_save_path.setText(self.save_directory)
                
                # 加载HEX显示设置
                if 'hex_recv' in config:
                    self.check_hex_recv.setChecked(config['hex_recv'])
                
                # 加载HEX发送设置
                if 'hex_send' in config:
                    self.check_hex_send.setChecked(config['hex_send'])
                
                # 加载显示时间戳设置
                if 'show_timestamp' in config:
                    self.check_timestamp.setChecked(config['show_timestamp'])
                
                # 加载首字段
                if 'head_field' in config:
                    self.text_ota.setText(config['head_field'])
                
                # 加载尾字段
                if 'tail_field' in config:
                    self.text_tail.setText(config['tail_field'])
                
                # 加载回车换行设置
                if 'newline' in config:
                    self.check_newline.setChecked(config['newline'])
                
                # 加载RTS设置
                if 'rts' in config:
                    self.check_rts.setChecked(config['rts'])
                
                # 加载DTR设置
                if 'dtr' in config:
                    self.check_dtr.setChecked(config['dtr'])
                
                # 加载多字符发送项目
                if 'multi_items' in config:
                    # 清空现有项目
                    self.table_multi_send.setRowCount(0)
                    
                    # 添加保存的项目
                    for item_data in config['multi_items']:
                        row = self.table_multi_send.rowCount()
                        self.table_multi_send.insertRow(row)
                        
                        # HEX复选框
                        hex_checkbox = QCheckBox()
                        hex_checkbox.setChecked(item_data.get('hex', False))
                        hex_widget = QWidget()
                        hex_layout = QHBoxLayout(hex_widget)
                        hex_layout.setContentsMargins(5, 0, 5, 0)
                        hex_layout.addWidget(hex_checkbox)
                        self.table_multi_send.setCellWidget(row, 0, hex_widget)
                        
                        # 字符串输入框
                        string_item = QTableWidgetItem(item_data.get('string', ''))
                        self.table_multi_send.setItem(row, 1, string_item)
                        
                        # 发送按钮
                        button = QPushButton(item_data.get('button_text', '无注释'))
                        button.setFont(QFont("Microsoft YaHei", 9))
                        button.setMinimumWidth(70)  # 调整按钮宽度
                        button.clicked.connect(lambda checked, r=row: self.send_multi_item(r))
                        # 为按钮设置唯一的对象名称，用于事件过滤器
                        button.setObjectName(f"send_button_{row}")
                        # 安装事件过滤器以支持双击编辑
                        button.installEventFilter(self)
                        button_widget = QWidget()
                        button_layout = QHBoxLayout(button_widget)
                        button_layout.setContentsMargins(5, 0, 5, 0)
                        button_layout.addWidget(button)
                        self.table_multi_send.setCellWidget(row, 2, button_widget)
                        
                        # 延时设置
                        delay_spin = QSpinBox()
                        delay_spin.setRange(0, 999999)
                        delay_spin.setValue(item_data.get('delay', 1000))
                        delay_spin.setFont(QFont("Consolas", 9))
                        delay_widget = QWidget()
                        delay_layout = QHBoxLayout(delay_widget)
                        delay_layout.setContentsMargins(5, 0, 5, 0)
                        delay_layout.addWidget(delay_spin)
                        self.table_multi_send.setCellWidget(row, 3, delay_widget)
                        
                        # 顺序显示框
                        order_item = QTableWidgetItem(item_data.get('order', ''))
                        order_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        order_item.setTextAlignment(Qt.AlignCenter)  # 文本居中显示
                        self.table_multi_send.setItem(row, 4, order_item)
                
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
            # 获取HEX复选框状态
            hex_widget = self.table_multi_send.cellWidget(i, 0)
            hex_layout = hex_widget.layout()
            hex_checkbox = hex_layout.itemAt(0).widget()
            is_hex = hex_checkbox.isChecked()
            
            # 获取字符串
            string_item = self.table_multi_send.item(i, 1)
            string = string_item.text() if string_item else ""
            
            # 获取按钮文本
            button_widget = self.table_multi_send.cellWidget(i, 2)
            button_layout = button_widget.layout()
            button = button_layout.itemAt(0).widget()
            button_text = button.text()
            
            # 获取延时
            delay_widget = self.table_multi_send.cellWidget(i, 3)
            delay_layout = delay_widget.layout()
            delay_spin = delay_layout.itemAt(0).widget()
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
        
        config = {
            'port': self.combo_port.currentText(),
            'baudrate': baudrate,
            'auto_save': self.check_auto_save.isChecked(),
            'save_directory': self.save_directory,
            'hex_recv': self.check_hex_recv.isChecked(),
            'hex_send': self.check_hex_send.isChecked(),
            'show_timestamp': self.check_timestamp.isChecked(),
            'newline': self.check_newline.isChecked(),
            'rts': self.check_rts.isChecked(),
            'dtr': self.check_dtr.isChecked(),
            'head_field': self.text_ota.text(),
            'tail_field': self.text_tail.text(),
            'multi_items': multi_items
        }
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            self.append_text("[系统]: 配置已保存\n")
        except Exception as e:
            self.append_text(f"[错误]: 保存配置失败: {str(e)}\n")
    
    def closeEvent(self, event):
        """窗口关闭事件处理"""
        # 停止批量发送线程
        if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
            try:
                self.batch_thread.stop()
                self.append_text("[系统]: 批量发送线程已停止\n")
            except Exception as e:
                self.append_text(f"[错误]: 停止批量发送线程失败: {str(e)}\n")
        
        # 停止接收线程
        if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
            try:
                self.read_thread.stop()
                self.append_text("[系统]: 接收线程已停止\n")
            except Exception as e:
                self.append_text(f"[错误]: 停止接收线程失败: {str(e)}\n")
        
        # 关闭串口
        if hasattr(self, 'serial_port') and self.serial_port:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
                    self.append_text("[系统]: 串口已关闭\n")
            except Exception as e:
                self.append_text(f"[错误]: 关闭串口失败: {str(e)}\n")
        
        # 关闭日志文件
        if hasattr(self, 'current_log_file') and self.current_log_file:
            try:
                self.current_log_file.close()
                self.append_text("[系统]: 日志文件已关闭\n")
            except Exception as e:
                print(f"关闭日志文件失败: {e}")
        
        # 保存配置
        try:
            self.save_config()
        except Exception as e:
            print(f"保存配置失败: {e}")
        
        # 关闭线程池
        if hasattr(self, 'thread_pool'):
            self.thread_pool.clear()
            self.thread_pool.waitForDone()
        
        event.accept()
    
    def __del__(self):
        """析构函数，确保资源清理"""
        # 停止批量发送线程
        if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
            try:
                self.batch_thread.stop()
            except:
                pass
        
        # 停止接收线程
        if hasattr(self, 'read_thread') and self.read_thread.isRunning():
            try:
                self.read_thread.stop()
            except:
                pass
        
        # 关闭串口
        if hasattr(self, 'serial_port') and self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
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
