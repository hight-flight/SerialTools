import sys
import os
import datetime
import serial
import serial.tools.list_ports
from collections import deque
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QPushButton, 
                             QTextEdit, QCheckBox, QMessageBox, QSplitter, QSpinBox, QLineEdit, QProgressBar, QGroupBox, QDialog, QFormLayout, 
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QInputDialog, QFrame, QSizePolicy)  
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QRunnable, QThreadPool, QObject, QMetaObject, Q_ARG, pyqtSlot, QMutex, QMutexLocker
from PyQt5.QtGui import QFont, QTextCursor, QTextCharFormat, QColor

# --- 全局常量 --- 
VERSION = "1.1.5"

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

# --- 串口接收线程 ---
class SerialReadThread(QThread):
    # 定义一个信号，用于将接收到的数据传递给主界面
    receive_data_signal = pyqtSignal(bytes)
    # 定义一个信号，用于将错误信息传递给主界面
    error_signal = pyqtSignal(str)

    def __init__(self, serial_port, serial_mutex):
        super().__init__()
        self.serial_port = serial_port
        self.running = True
        self.running_lock = QMutex()  # 保护running标志的锁
        self.serial_mutex = serial_mutex  # 串口操作互斥锁

    def is_running(self):
        """线程安全地检查running状态"""
        self.running_lock.lock()
        result = self.running
        self.running_lock.unlock()
        return result

    def set_running(self, value):
        """线程安全地设置running状态"""
        self.running_lock.lock()
        self.running = value
        self.running_lock.unlock()

    def run(self):
        """
        线程运行的主函数，持续循环执行直到running标志为False
        负责从串口读取数据，并通过信号发射给其他组件
        使用自适应休眠：有数据时快速轮询(1ms)，无数据时降低频率(20ms)
        """
        try:
            while self.is_running():
                try:
                    data_read = False
                    with QMutexLocker(self.serial_mutex):
                        if not self.serial_port or not self.serial_port.is_open:
                            error_msg = "串口已关闭"
                            self.error_signal.emit(error_msg)
                            self.set_running(False)
                            break

                        if self.serial_port.in_waiting > 0:
                            try:
                                data = self.serial_port.read(self.serial_port.in_waiting)
                                if data:
                                    self.receive_data_signal.emit(data)
                                    data_read = True
                            except serial.SerialTimeoutException:
                                pass
                            except serial.SerialException as e:
                                error_msg = f"串口读取异常: {e}"
                                self.error_signal.emit(error_msg)
                                self.set_running(False)
                                break

                    # 自适应休眠：有数据时短暂休眠让出CPU，无数据时稍长休眠降低占用
                    if data_read:
                        self.msleep(1)
                    else:
                        self.msleep(20)
                except serial.SerialException as e:
                    error_msg = f"串口错误: {e}"
                    print(error_msg)
                    self.error_signal.emit(error_msg)
                    self.set_running(False)
                    break
                except ValueError as e:
                    error_msg = f"数据解析错误: {e}"
                    print(error_msg)
                    self.error_signal.emit(error_msg)
                    self.msleep(100)
                except TimeoutError as e:
                    error_msg = f"超时错误: {e}"
                    print(error_msg)
                    self.error_signal.emit(error_msg)
                    self.msleep(100)
                except KeyboardInterrupt:
                    error_msg = "用户中断操作"
                    print(error_msg)
                    self.error_signal.emit(error_msg)
                    self.set_running(False)
                    break
                except SystemExit:
                    error_msg = "系统退出"
                    print(error_msg)
                    self.error_signal.emit(error_msg)
                    self.set_running(False)
                    break
                except MemoryError:
                    error_msg = "内存错误"
                    print(error_msg)
                    self.error_signal.emit(error_msg)
                    self.set_running(False)
                    break
                except Exception as e:
                    error_msg = f"读取错误: {e}"
                    print(error_msg)
                    self.error_signal.emit(error_msg)
                    self.msleep(100)
        finally:
            # 确保线程能够正确停止
            self.set_running(False)

    def stop(self):
        """停止线程，设置超时避免无限等待"""
        self.set_running(False)
        # 使用带超时的wait()，避免线程卡在serial.read()时无限阻塞
        if not self.wait(2000):  # 2秒超时
            print(f"警告: 线程停止超时，可能卡在串口操作中")

# --- 主窗口类 ---
class SerialTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.serial_port = None
        self.read_thread = None
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
        
        # 配置文件路径
        self.config_file = os.path.join(os.getcwd(), "serial_config.json")
        
        # 线程安全相关
        self.serial_mutex = QMutex()  # 串口操作互斥锁
        self.error_state = False  # 错误状态标志
        self.stop_file_send = False  # 文件发送取消标志
        
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
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(3)

        # --- 顶部设置区域 ---
        # 串口设置分组
        serial_group = QGroupBox("串口设置")
        serial_group.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        serial_layout = QVBoxLayout(serial_group)
        serial_layout.setContentsMargins(4, 3, 4, 3)
        serial_layout.setSpacing(2)
        
        # 第一行：串口和波特率设置
        port_baud_layout = QHBoxLayout()
        port_baud_layout.setSpacing(4)
        
        # 串口选择
        port_layout = QHBoxLayout()
        port_label = QLabel("串口:")
        port_label.setFont(QFont("Microsoft YaHei", 8))
        port_layout.addWidget(port_label)
        self.combo_port = QComboBox()
        self.combo_port.setMinimumWidth(100)
        self.combo_port.setFont(QFont("Consolas", 8))
        port_layout.addWidget(self.combo_port)
        port_baud_layout.addLayout(port_layout)
        
        # 刷新按钮
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setMaximumWidth(55)
        self.btn_refresh.setFont(QFont("Microsoft YaHei", 8))
        self.btn_refresh.clicked.connect(self.refresh_ports)
        port_baud_layout.addWidget(self.btn_refresh)
        
        # 波特率选择
        baud_layout = QHBoxLayout()
        baud_label = QLabel("波特率:")
        baud_label.setFont(QFont("Microsoft YaHei", 8))
        baud_layout.addWidget(baud_label)
        self.combo_baud = QComboBox()
        self.combo_baud.addItems(['9600', '19200', '38400', '57600', '115200', '自定义'])
        self.combo_baud.setCurrentText('115200')
        self.combo_baud.setFont(QFont("Consolas", 8))
        self.combo_baud.currentIndexChanged.connect(self.handle_baud_change)
        baud_layout.addWidget(self.combo_baud)
        port_baud_layout.addLayout(baud_layout)
                        
        # 打开/关闭串口按钮
        self.btn_switch = QPushButton("打开串口")
        self.btn_switch.setCheckable(True)
        self.btn_switch.setFont(QFont("Microsoft YaHei", 8))
        self.btn_switch.setMinimumWidth(80)
        self.btn_switch.clicked.connect(self.toggle_serial)
        port_baud_layout.addWidget(self.btn_switch)
        
        # 更多串口设置按钮
        self.btn_more_settings = QPushButton("更多串口设置")
        self.btn_more_settings.setFont(QFont("Microsoft YaHei", 8))
        self.btn_more_settings.setMinimumWidth(90)
        self.btn_more_settings.clicked.connect(self.show_more_settings)
        port_baud_layout.addWidget(self.btn_more_settings)
        port_baud_layout.addStretch()

        serial_layout.addLayout(port_baud_layout)
        
        # 第二行：显示和自动保存设置
        display_save_layout = QHBoxLayout()
        display_save_layout.setSpacing(4)

        # 接收区设置：HEX显示
        self.check_hex_recv = QCheckBox("HEX显示")
        self.check_hex_recv.setFont(QFont("Microsoft YaHei", 8))
        display_save_layout.addWidget(self.check_hex_recv)

        # 编码选择
        encoding_label = QLabel("编码:")
        encoding_label.setFont(QFont("Microsoft YaHei", 8))
        display_save_layout.addWidget(encoding_label)
        self.combo_encoding = QComboBox()
        self.combo_encoding.addItems(['UTF-8', 'GBK', 'GB2312', 'ASCII', 'ISO-8859-1', 'GB18030'])
        self.combo_encoding.setCurrentText('UTF-8')
        self.combo_encoding.setFont(QFont("Microsoft YaHei", 8))
        self.combo_encoding.setMinimumWidth(80)
        display_save_layout.addWidget(self.combo_encoding)

        # 显示时间戳复选框
        self.check_timestamp = QCheckBox("显示时间")
        self.check_timestamp.setChecked(True)  # 默认显示时间
        self.check_timestamp.setFont(QFont("Microsoft YaHei", 8))
        display_save_layout.addWidget(self.check_timestamp)

        # 自动保存复选框
        self.check_auto_save = QCheckBox("自动保存日志")
        self.check_auto_save.setFont(QFont("Microsoft YaHei", 8))
        self.check_auto_save.stateChanged.connect(self.toggle_auto_save)
        display_save_layout.addWidget(self.check_auto_save)

        display_save_layout.addSpacing(6)

        # 保存路径（紧凑内联）
        self.label_save_path = QLabel("路径:")
        self.label_save_path.setFont(QFont("Microsoft YaHei", 8))
        display_save_layout.addWidget(self.label_save_path)
        self.line_edit_save_path = QLineEdit()
        self.line_edit_save_path.setReadOnly(True)
        self.line_edit_save_path.setText(self.save_directory)
        self.line_edit_save_path.setFont(QFont("Consolas", 8))
        self.line_edit_save_path.setMaximumWidth(200)
        display_save_layout.addWidget(self.line_edit_save_path)
        self.btn_browse_path = QPushButton("浏览")
        self.btn_browse_path.setMaximumWidth(40)
        self.btn_browse_path.setFont(QFont("Microsoft YaHei", 8))
        self.btn_browse_path.clicked.connect(self.browse_save_path)
        display_save_layout.addWidget(self.btn_browse_path)

        # 清空接收区按钮
        self.btn_clear_recv = QPushButton("清空接收")
        self.btn_clear_recv.setFont(QFont("Microsoft YaHei", 8))
        self.btn_clear_recv.setFixedSize(60, 22)
        self.btn_clear_recv.clicked.connect(self.clear_recv_area)
        display_save_layout.addWidget(self.btn_clear_recv)
        display_save_layout.addStretch()

        serial_layout.addLayout(display_save_layout)
        
        main_layout.addWidget(serial_group)

        # --- 中间接收和发送区域（使用分割器）---
        splitter = QSplitter(Qt.Vertical)
        
        # 接收区
        recv_group = QGroupBox("接收区")
        recv_group.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        recv_layout = QVBoxLayout(recv_group)
        recv_layout.setContentsMargins(3, 3, 3, 3)

        self.text_recv = QTextEdit()
        self.text_recv.setReadOnly(True)
        self.text_recv.setFont(QFont("Consolas", 11))
        # 设置背景色为白色，文本颜色为黑色
        self.text_recv.setStyleSheet("QTextEdit { background-color: white; color: black; border: 1px solid #CCCCCC; border-radius: 4px; }")
        recv_layout.addWidget(self.text_recv)

        # 图示区域
        graph_group = QGroupBox("数据统计")
        graph_group.setFont(QFont("Microsoft YaHei", 7, QFont.Bold))
        graph_layout = QHBoxLayout(graph_group)
        graph_layout.setContentsMargins(3, 2, 3, 2)
        graph_layout.setSpacing(8)
        
        # 接收字节数统计
        self.label_rx_bytes = QLabel("接收字节: 0")
        self.label_rx_bytes.setFont(QFont("Consolas", 7))
        graph_layout.addWidget(self.label_rx_bytes)

        # 发送字节数统计
        self.label_tx_bytes = QLabel("发送字节: 0")
        self.label_tx_bytes.setFont(QFont("Consolas", 7))
        graph_layout.addWidget(self.label_tx_bytes)

        # 数据包数量统计
        self.label_packets = QLabel("数据包: 0")
        self.label_packets.setFont(QFont("Consolas", 7))
        graph_layout.addWidget(self.label_packets)
        
        graph_layout.addStretch()
        
        recv_layout.addWidget(graph_group)
        splitter.addWidget(recv_group)
        
        # 发送区
        send_group = QGroupBox("发送区")
        send_group.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
        send_layout = QVBoxLayout(send_group)
        send_layout.setContentsMargins(4, 3, 4, 3)
        send_layout.setSpacing(2)
        
        # 发送设置行
        send_settings_layout = QHBoxLayout()
        send_settings_layout.setSpacing(3)

        # 发送区设置：HEX发送
        self.check_hex_send = QCheckBox("HEX发送")
        self.check_hex_send.setFont(QFont("Microsoft YaHei", 8))
        send_settings_layout.addWidget(self.check_hex_send)

        # 回车换行勾选选项
        self.check_newline = QCheckBox("回车换行")
        self.check_newline.setFont(QFont("Microsoft YaHei", 8))
        send_settings_layout.addWidget(self.check_newline)

        # RTS和DTR控制选项
        self.check_rts = QCheckBox("RTS")
        self.check_rts.setFont(QFont("Microsoft YaHei", 8))
        self.check_rts.stateChanged.connect(self.update_rts_dtr)
        send_settings_layout.addWidget(self.check_rts)

        self.check_dtr = QCheckBox("DTR")
        self.check_dtr.setFont(QFont("Microsoft YaHei", 8))
        self.check_dtr.stateChanged.connect(self.update_rts_dtr)
        send_settings_layout.addWidget(self.check_dtr)

        # 校验选项（使用子布局，设置更小的间距）
        checksum_layout = QHBoxLayout()
        checksum_layout.setSpacing(1)

        self.label_checksum = QLabel("校验:")
        self.label_checksum.setFont(QFont("Microsoft YaHei", 8))
        checksum_layout.addWidget(self.label_checksum)

        self.combo_checksum = QComboBox()
        self.combo_checksum.addItems(["None", "ModbusCRC16", "CRC32", "Fletcher", "XOR8", "ADD8", "ADD16"])
        self.combo_checksum.setFont(QFont("Consolas", 8))
        checksum_layout.addWidget(self.combo_checksum)

        send_settings_layout.addLayout(checksum_layout)

        # 重复发送相关控件
        repeat_layout = QHBoxLayout()
        repeat_layout.setSpacing(3)

        self.check_repeat = QCheckBox("重复发送")
        self.check_repeat.setFont(QFont("Microsoft YaHei", 8))
        repeat_layout.addWidget(self.check_repeat)

        repeat_layout.addWidget(QLabel("间隔(ms):"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(100, 5000)
        self.spin_interval.setValue(1000)
        self.spin_interval.setFont(QFont("Consolas", 8))
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
        head_field_layout.setSpacing(2)
        self.check_head_field = QCheckBox()
        head_field_layout.addWidget(self.check_head_field)
        head_label = QLabel("首字段:")
        head_label.setFont(QFont("Microsoft YaHei", 8))
        head_field_layout.addWidget(head_label)
        self.text_ota = QLineEdit()
        self.text_ota.setFont(QFont("Consolas", 8))
        self.text_ota.setPlaceholderText("输入首字段...")
        self.text_ota.setStyleSheet("QLineEdit { border: 1px solid #CCCCCC; border-radius: 4px; }")
        head_field_layout.addWidget(self.text_ota)
        fields_layout.addLayout(head_field_layout)

        # 发送尾字段
        tail_field_layout = QHBoxLayout()
        tail_field_layout.setSpacing(2)
        self.check_tail_field = QCheckBox()
        tail_field_layout.addWidget(self.check_tail_field)
        tail_label = QLabel("尾字段:")
        tail_label.setFont(QFont("Microsoft YaHei", 8))
        tail_field_layout.addWidget(tail_label)
        self.text_tail = QLineEdit()
        self.text_tail.setFont(QFont("Consolas", 8))
        self.text_tail.setPlaceholderText("输入尾字段...")
        self.text_tail.setStyleSheet("QLineEdit { border: 1px solid #CCCCCC; border-radius: 4px; }")
        tail_field_layout.addWidget(self.text_tail)
        fields_layout.addLayout(tail_field_layout)
        fields_layout.addStretch()

        send_layout.addLayout(fields_layout)
        
        # 发送输入框
        self.text_send = QTextEdit()
        self.text_send.setMaximumHeight(45)
        self.text_send.setFont(QFont("Consolas", 10))
        self.text_send.setPlaceholderText("在此输入要发送的内容...")
        self.text_send.setStyleSheet("QTextEdit { border: 1px solid #CCCCCC; border-radius: 4px; }")
        send_layout.addWidget(self.text_send)

        # 文件发送区域
        file_send_layout = QHBoxLayout()
        file_send_layout.setSpacing(4)
        
        # 文件路径显示
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setFont(QFont("Consolas", 8))
        self.file_path_edit.setPlaceholderText("选择要发送的文件...")
        self.file_path_edit.setStyleSheet("QLineEdit { border: 1px solid #CCCCCC; border-radius: 4px; }")
        self.file_path_edit.setReadOnly(True)

        self.btn_select_file = QPushButton("选择文件")
        self.btn_select_file.setFont(QFont("Microsoft YaHei", 8))
        self.btn_select_file.setMinimumWidth(60)
        self.btn_select_file.clicked.connect(self.select_file_to_send)

        self.btn_send_file = QPushButton("发送文件")
        self.btn_send_file.setFont(QFont("Microsoft YaHei", 8))
        self.btn_send_file.setMinimumWidth(60)
        self.btn_send_file.clicked.connect(self.send_file)
        self.btn_send_file.setEnabled(False)  # 默认禁用，选择文件后启用
        
        file_send_layout.addWidget(self.file_path_edit)
        file_send_layout.addWidget(self.btn_select_file)
        file_send_layout.addWidget(self.btn_send_file)
        file_send_layout.addStretch()
        send_layout.addLayout(file_send_layout)
        
        # 发送按钮行
        send_buttons_layout = QHBoxLayout()
        send_buttons_layout.setSpacing(4)

        self.btn_send = QPushButton("发送")
        self.btn_send.setFont(QFont("Microsoft YaHei", 8))
        self.btn_send.setMinimumWidth(50)
        self.btn_send.clicked.connect(self.send_data)
        self.btn_send.setShortcut("Ctrl+Return")

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setFont(QFont("Microsoft YaHei", 8))
        self.btn_stop.setMinimumWidth(50)
        self.btn_stop.clicked.connect(self.stop_repeat)
        self.btn_stop.setEnabled(False)

        self.btn_clear_send = QPushButton("清空发送")
        self.btn_clear_send.setFont(QFont("Microsoft YaHei", 8))
        self.btn_clear_send.setMinimumWidth(50)
        self.btn_clear_send.clicked.connect(self.clear_send_area)

        send_buttons_layout.addWidget(self.btn_send)
        send_buttons_layout.addWidget(self.btn_stop)
        send_buttons_layout.addWidget(self.btn_clear_send)
        send_buttons_layout.addStretch()

        self.btn_save_params = QPushButton("保存参数")
        self.btn_save_params.setFont(QFont("Microsoft YaHei", 8))
        self.btn_save_params.setMinimumWidth(60)
        self.btn_save_params.clicked.connect(self.save_config)
        send_buttons_layout.addWidget(self.btn_save_params)

        self.btn_toggle_multi_send = QPushButton("显示多字符发送")
        self.btn_toggle_multi_send.setFont(QFont("Microsoft YaHei", 8))
        self.btn_toggle_multi_send.setMinimumWidth(100)
        self.btn_toggle_multi_send.clicked.connect(self.toggle_multi_send)
        send_buttons_layout.addWidget(self.btn_toggle_multi_send)
        
        send_layout.addLayout(send_buttons_layout)
        splitter.addWidget(send_group)
        
        # 设置分割器的初始大小比例，接收区占绝大部分，发送区紧凑
        splitter.setSizes([2400, 100])
        
        # 添加多字符发送区域（默认隐藏）
        self.multi_send_widget = QWidget()
        self.multi_send_layout = QVBoxLayout(self.multi_send_widget)
        self.multi_send_layout.setContentsMargins(0, 0, 0, 0)
        self.multi_send_layout.setSpacing(10)
        
        # 多字符发送组
        multi_send_group = QGroupBox("多字符串发送")
        multi_send_group.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
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
        delay_label = QLabel("延时:")
        delay_label.setFont(QFont("Microsoft YaHei", 8))
        toolbar_layout.addWidget(delay_label)
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 10000)
        self.spin_delay.setValue(1000)
        self.spin_delay.setFont(QFont("Consolas", 8))
        self.spin_delay.setMinimumWidth(60)
        toolbar_layout.addWidget(self.spin_delay)
        ms_label = QLabel("ms")
        ms_label.setFont(QFont("Microsoft YaHei", 8))
        toolbar_layout.addWidget(ms_label)
        
        # 循环次数勾选按钮和输入框
        self.check_cycle_count = QCheckBox("次数:")
        self.check_cycle_count.setFont(QFont("Microsoft YaHei", 8))
        toolbar_layout.addWidget(self.check_cycle_count)
        
        self.spin_cycle_count = QSpinBox()
        self.spin_cycle_count.setRange(1, 9999)
        self.spin_cycle_count.setValue(1)
        self.spin_cycle_count.setFont(QFont("Consolas", 8))
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
        btn_save.setFont(QFont("Microsoft YaHei", 8))
        btn_save.setMinimumWidth(50)
        btn_save.clicked.connect(self.save_multi_items)
        button_row_layout.addWidget(btn_save)
        
        btn_load = QPushButton("加载")
        btn_load.setFont(QFont("Microsoft YaHei", 8))
        btn_load.setMinimumWidth(50)
        btn_load.clicked.connect(self.load_multi_items)
        button_row_layout.addWidget(btn_load)
        
        # 帮助按钮
        btn_help = QPushButton("帮助")
        btn_help.setFont(QFont("Microsoft YaHei", 8))
        btn_help.setMinimumWidth(50)
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
            send_btn.clicked.connect(self.on_send_multi_btn_clicked)
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
        btn_add.setFont(QFont("Microsoft YaHei", 8))
        btn_add.setMinimumWidth(30)
        btn_add.clicked.connect(self.add_multi_item)
        btn_layout.addWidget(btn_add)
        
        btn_remove = QPushButton("-")
        btn_remove.setFont(QFont("Microsoft YaHei", 8))
        btn_remove.setMinimumWidth(30)
        btn_remove.clicked.connect(self.remove_multi_item)
        btn_layout.addWidget(btn_remove)
        
        # 清空指令按钮
        btn_clear = QPushButton("清空指令")
        btn_clear.setFont(QFont("Microsoft YaHei", 8))
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
        left_layout.setSpacing(3)
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
        self.statusBar().showMessage("就绪")
        self.statusBar().setStyleSheet("QStatusBar { background-color: #F0F0F0; border-top: 1px solid #CCCCCC; }")
        
        # 添加状态栏组件
        # 使用富文本设置连接状态，只改变状态部分的颜色
        self.status_connection = QLabel()
        self.status_connection.setFont(QFont("Microsoft YaHei", 9))
        # 初始状态为未连接，红色
        self.status_connection.setText('<span style="color: black;">连接状态：</span><span style="color: red;">未连接</span>')
        self.status_baud = QLabel("波特率: 115200")
        self.status_baud.setFont(QFont("Microsoft YaHei", 9))
        self.status_log = QLabel("日志文件: 未创建")
        self.status_log.setFont(QFont("Microsoft YaHei", 9))
        
        self.statusBar().addPermanentWidget(self.status_connection)
        self.statusBar().addPermanentWidget(QLabel(" | "))
        self.statusBar().addPermanentWidget(self.status_baud)
        self.statusBar().addPermanentWidget(QLabel(" | "))
        self.statusBar().addPermanentWidget(self.status_log)
        self.statusBar().addPermanentWidget(QLabel(" | "))
        
        # 版本号显示
        self.status_version = QLabel(f"版本: {VERSION}")
        self.status_version.setFont(QFont("Microsoft YaHei", 9))
        self.statusBar().addPermanentWidget(self.status_version)

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
                    else:
                        # 如果不存在，在"自定义"选项之前插入新的波特率
                        self.combo_baud.insertItem(index, baud_str)
                        # 选中新插入的波特率（此时index位置就是新插入的项）
                        self.combo_baud.setCurrentIndex(index)
                else:
                    # 如果用户取消输入，恢复到之前的波特率
                    self.combo_baud.setCurrentText(current_baud)
            finally:
                # 重新连接信号
                self.combo_baud.currentIndexChanged.connect(self.handle_baud_change)

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
                # 停止旧的接收线程（在关闭串口之前）
                if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
                    try:
                        self.read_thread.stop()
                        self.read_thread = None
                    except Exception as e:
                        self.append_text(f"[错误]: 停止旧接收线程失败: {str(e)}\n")

                # 关闭之前可能存在的串口连接
                with QMutexLocker(self.serial_mutex):
                    if hasattr(self, 'serial_port') and self.serial_port:
                        try:
                            if self.serial_port.is_open:
                                self.serial_port.close()
                        except Exception as e:
                            self.append_text(f"[错误]: 关闭旧串口失败: {str(e)}\n")
                        finally:
                            self.serial_port = None
                
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
                
                # 流控制 — serial.Serial() 接受 xonxoff/rtscts/dsrdtr 布尔参数
                try:
                    flow_control = self.serial_flow_control if hasattr(self, 'serial_flow_control') else 'None'
                except AttributeError:
                    flow_control = 'None'

                xonxoff = (flow_control == 'Xon/Xoff')
                rtscts = (flow_control == 'RTS/CTS')
                dsrdtr = (flow_control == 'DSR/DTR')

                self.serial_port = serial.Serial(
                    port=port_name,
                    baudrate=baud_rate,
                    bytesize=bytesize,
                    parity=parity,
                    stopbits=stopbits,
                    timeout=1.0,
                    xonxoff=xonxoff,
                    rtscts=rtscts,
                    dsrdtr=dsrdtr
                )
                
                # 设置RTS和DTR的初始状态
                try:
                    self.serial_port.rts = self.check_rts.isChecked()
                    self.serial_port.dtr = self.check_dtr.isChecked()
                except Exception as e:
                    self.append_text(f"[警告]: 设置RTS/DTR状态失败: {str(e)}\n")
                
                # 启动接收线程
                self.read_thread = SerialReadThread(self.serial_port, self.serial_mutex)
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
                self.status_connection.setText('<span style="color: black;">连接状态：</span><span style="color: green;">已连接</span>')
                self.status_baud.setText(f"波特率: {baud_rate}")
                self.statusBar().showMessage("就绪")  # 恢复就绪状态
                
                # 清除错误状态
                self.error_state = False

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
                    finally:
                        self.serial_port = None  # 清空串口引用
                
                # 确保线程被停止
                if hasattr(self, 'read_thread') and self.read_thread:
                    try:
                        self.read_thread.stop()
                    except:
                        pass
                
                # 确保批量发送线程被停止
                if hasattr(self, 'batch_thread') and self.batch_thread and self.batch_thread.isRunning():
                    try:
                        self.batch_thread.stop()
                        self.check_cycle_send.setChecked(False)
                    except:
                        pass
                
                # 更新状态栏显示错误
                self.statusBar().showMessage(f"打开失败: {error_msg}")
                
                # 设置错误状态
                self.error_state = True
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
            self.status_connection.setText('<span style="color: black;">连接状态：</span><span style="color: red;">未连接</span>')
            self.status_baud.setText("波特率：115200")
            self.statusBar().showMessage("就绪")  # 恢复就绪状态
            
            # 清除错误状态
            self.error_state = False
            
            # 恢复 UI 状态
            self.btn_switch.setText("打开串口")
            self.combo_port.setEnabled(True)
            self.combo_baud.setEnabled(True)
            self.btn_refresh.setEnabled(True)

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
        if not hasattr(self, 'serial_port') or not self.serial_port or not self.serial_port.is_open:
            QMessageBox.warning(self, "警告", "请先打开串口！")
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
                            if hasattr(self, 'serial_port') and self.serial_port and self.serial_port.is_open:
                                self.serial_port.write(data)
                                self.serial_port.flush()
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
    
    def on_file_send_error(self, error):
        """文件发送错误回调"""
        self.append_text(f"[错误]: 文件发送错误: {error}\n")
        self.btn_send_file.setEnabled(True)
    
    def on_file_send_progress(self, progress_msg):
        """文件发送进度回调"""
        self.append_text(f"{progress_msg}\n")

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
                    data = (content + '\r\n').encode(encoding)
                else:
                    data = content.encode(encoding)

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
                            bytes_sent = self.serial_port.write(data_with_checksum)
                        # 更新发送数据统计
                        self.tx_bytes += bytes_sent
                        self.label_tx_bytes.setText(f"发送字节: {self.tx_bytes}")
                        # 显示发送的内容和校验值
                        checksum_hex = ' '.join([f'{byte:02X}' for byte in checksum])
                        self.append_text(f"[发送]: {content}\n")
                        self.append_text(f"[校验]: {checksum_type} = {checksum_hex}\n")
                        self.append_text(f"[系统]: 已发送 {bytes_sent} 字节（含校验值）\n")
                    except serial.SerialException as e:
                        error_msg = f"发送数据失败: {e}"
                        QMessageBox.warning(self, "发送失败", error_msg)
                        self.append_text(f"[错误]: {error_msg}\n")
            else:
                # 发送原始数据（使用互斥锁保护）
                try:
                    with QMutexLocker(self.serial_mutex):
                        bytes_sent = self.serial_port.write(data)
                    # 更新发送数据统计
                    self.tx_bytes += bytes_sent
                    self.label_tx_bytes.setText(f"发送字节: {self.tx_bytes}")
                    # 在接收区显示发送的内容，让用户能看到发送状态
                    self.append_text(f"[发送]: {content}\n")
                    self.append_text(f"[系统]: 已发送 {bytes_sent} 字节\n")
                except serial.SerialException as e:
                    error_msg = f"发送数据失败: {e}"
                    QMessageBox.warning(self, "发送失败", error_msg)
                    self.append_text(f"[错误]: {error_msg}\n")
            
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
        # 先保存错误信息，后续在UI线程中显示
        self.error_state = True  # 设置错误状态标志
        
        # 使用 try-except 包裹锁操作，防止锁获取失败导致程序崩溃
        try:
            # 在锁内只执行必要的串口关闭操作（避免死锁）
            with QMutexLocker(self.serial_mutex):
                # 关闭串口（最重要的操作，先执行）
                if hasattr(self, 'serial_port') and self.serial_port:
                    try:
                        if self.serial_port.is_open:
                            self.serial_port.close()
                    except Exception as e:
                        pass
                    finally:
                        self.serial_port = None  # 清空串口引用
        except Exception as e:
            # 锁获取失败，尝试直接关闭串口
            if hasattr(self, 'serial_port') and self.serial_port:
                try:
                    if self.serial_port.is_open:
                        self.serial_port.close()
                except Exception:
                    pass
                finally:
                    self.serial_port = None
        
        # 在锁外停止线程（避免死锁）
        try:
            # 停止批量发送线程
            if hasattr(self, 'batch_thread') and self.batch_thread.isRunning():
                self.batch_thread.stop()
        
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
            
            # 更新UI状态
            self.btn_switch.setText("打开串口")
            self.btn_switch.setChecked(False)
            self.combo_port.setEnabled(True)
            self.combo_baud.setEnabled(True)
            self.btn_refresh.setEnabled(True)
            
            # 更新状态栏
            self.status_connection.setText('<span style="color: black;">连接状态：</span><span style="color: red;">未连接</span>')
            self.status_baud.setText("波特率: 115200")
            self.statusBar().showMessage(f"串口读取错误: {error_msg}")
        except Exception as e:
            # UI更新失败不影响主要流程
            pass
        
        # 错误状态保持为True，直到用户重新连接串口成功
    
    def cleanup_resources(self):
        """清理所有资源"""
        # 停止定时器
        if hasattr(self, 'repeat_timer') and self.repeat_timer and self.repeat_timer.isActive():
            self.repeat_timer.stop()
        
        # 停止批量发送线程（在锁外停止，避免死锁）
        if hasattr(self, 'batch_thread') and self.batch_thread and self.batch_thread.isRunning():
            try:
                self.batch_thread.stop()
            except Exception as e:
                print(f"停止批量发送线程失败: {e}")
            finally:
                self.batch_thread = None
        
        # 停止接收线程并关闭串口（需要互斥锁保护）
        with QMutexLocker(self.serial_mutex):
            # 停止接收线程
            if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.isRunning():
                try:
                    self.read_thread.stop()
                except Exception as e:
                    print(f"停止接收线程失败: {e}")
                finally:
                    self.read_thread = None
            
            # 关闭串口
            if hasattr(self, 'serial_port') and self.serial_port:
                try:
                    if self.serial_port.is_open:
                        self.serial_port.close()
                except Exception as e:
                    print(f"关闭串口失败: {e}")
                finally:
                    self.serial_port = None
        
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
        # 保存配置
        self.save_config()
        # 清理所有资源（cleanup_resources 已经包含了所有必要的清理）
        self.cleanup_resources()

        # 接受关闭事件
        event.accept()

    def handle_receive_data(self, data):
        """处理接收到的数据"""
        try:
            # 限制单次处理的数据大小，防止缓冲区溢出
            max_single_process = 1024 * 1024  # 1MB
            if len(data) > max_single_process:
                # 大数据包分段处理
                chunks = [data[i:i+max_single_process] for i in range(0, len(data), max_single_process)]
                for chunk in chunks:
                    self._handle_receive_data_chunk(chunk)
                return
            
            # 更新接收数据统计
            self.rx_bytes += len(data)
            self.packets += 1
            self.label_rx_bytes.setText(f"接收字节: {self.rx_bytes}")
            self.label_packets.setText(f"数据包: {self.packets}")
            
            # 获取当前时间戳
            timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")
            
            if self.check_hex_recv.isChecked():
                # HEX 显示
                hex_str = ' '.join([f'{byte:02X}' for byte in data])
                # 限制HEX字符串长度
                if len(hex_str) > self.MAX_LOG_ENTRY_LENGTH:
                    hex_str = hex_str[:self.MAX_LOG_ENTRY_LENGTH] + "...(截断)"
                # 添加到日志缓冲区（deque自动管理大小）
                log_entry = timestamp + hex_str
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
                
                # 限制文本长度
                if len(text) > self.MAX_LOG_ENTRY_LENGTH:
                    text = text[:self.MAX_LOG_ENTRY_LENGTH] + "...(截断)"
                # 添加到日志缓冲区（deque自动管理大小）
                log_entry = timestamp + text
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
    
    def _handle_receive_data_chunk(self, data):
        """处理数据块（用于大数据包分段处理）"""
        try:
            # 更新接收数据统计
            self.rx_bytes += len(data)
            self.label_rx_bytes.setText(f"接收字节: {self.rx_bytes}")

            timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")

            if self.check_hex_recv.isChecked():
                hex_str = ' '.join([f'{byte:02X}' for byte in data])
                if len(hex_str) > self.MAX_LOG_ENTRY_LENGTH:
                    hex_str = hex_str[:self.MAX_LOG_ENTRY_LENGTH] + "...(截断)"
                log_entry = timestamp + hex_str
                self.log_buffer.append(log_entry)
                if self.auto_save_enabled and self.current_log_file:
                    self.auto_save_data(log_entry + '\n')
            else:
                encoding = self.combo_encoding.currentText()
                try:
                    text = data.decode(encoding, errors='replace')
                except LookupError:
                    text = data.decode('utf-8', errors='replace')
                if len(text) > self.MAX_LOG_ENTRY_LENGTH:
                    text = text[:self.MAX_LOG_ENTRY_LENGTH] + "...(截断)"
                log_entry = timestamp + text
                self.log_buffer.append(log_entry)
                if self.auto_save_enabled and self.current_log_file:
                    self.auto_save_data(log_entry + '\n')
        except Exception:
            pass
    
    def _update_receive_display(self, text, timestamp):
        """更新接收区显示（限制更新频率，缓冲数据避免丢失）"""
        import time
        current_time = time.time()

        if not hasattr(self, '_last_display_update_time'):
            self._last_display_update_time = 0
        if not hasattr(self, '_pending_display_data'):
            self._pending_display_data = []

        # 将数据添加到待显示缓冲区
        self._pending_display_data.append((text, timestamp))

        # 至少间隔20ms更新一次UI（与读取线程20ms休眠匹配）
        if current_time - self._last_display_update_time < 0.02:
            return

        self._last_display_update_time = current_time

        # 清空待显示缓冲区并合并显示
        pending = self._pending_display_data
        self._pending_display_data = []

        for text_item, ts_item in pending:
            # 按换行符分割数据，确保每行一个时间戳
            # 先去除\r（串口设备常用\r\n结尾），再分割
            lines = text_item.replace('\r', '').split('\n')

            for i, line in enumerate(lines):
                # 跳过空行（连续换行或开头换行的情况）
                if not line:
                    continue
                    
                # 处理ANSI颜色转义序列和控制字符
                formatted_segments = self.process_ansi_colors(line)

                # 显示到界面
                if self.check_timestamp.isChecked():
                    # 获取光标位置
                    cursor = self.text_recv.textCursor()
                    cursor.movePosition(QTextCursor.End)
                    
                    # 检查是否已经在行首（避免重复换行）
                    if cursor.atBlockStart():
                        # 如果已经在新行开头，直接添加时间戳
                        cursor.insertText(ts_item)
                    else:
                        # 如果不在行首，先换行再添加时间戳
                        cursor.insertText('\n' + ts_item)

                # 再添加格式化文本
                self.append_formatted_text(formatted_segments)
                
                # 如果不是最后一行，添加换行符
                if i < len(lines) - 1 and line:
                    cursor = self.text_recv.textCursor()
                    cursor.movePosition(QTextCursor.End)
                    cursor.insertText('\n')

    def append_formatted_text(self, formatted_segments):
        """向接收区追加带格式的文本，并自动滚动到底部"""
        # 开始一个编辑块，提高性能
        cursor = self.text_recv.textCursor()
        cursor.beginEditBlock()
        
        # 检查行数限制，超过时删除前面的内容
        block_count = self.text_recv.document().blockCount()
        if block_count >= self.MAX_DISPLAY_LINES:
            # 删除前面的10%内容（约500行），保留大部分内容
            lines_to_remove = max(100, int(self.MAX_DISPLAY_LINES * 0.1))
            cursor.movePosition(QTextCursor.Start)
            for _ in range(lines_to_remove):
                cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor)
                if not cursor.atEnd():
                    cursor.removeSelectedText()
        
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
        
        # 检查行数限制，超过时删除前面的内容
        block_count = self.text_recv.document().blockCount()
        if block_count >= self.MAX_DISPLAY_LINES:
            # 删除前面的10%内容（约500行），保留大部分内容
            lines_to_remove = max(100, int(self.MAX_DISPLAY_LINES * 0.1))
            cursor.movePosition(QTextCursor.Start)
            for _ in range(lines_to_remove):
                cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor)
                if not cursor.atEnd():
                    cursor.removeSelectedText()
        
        # 移动到末尾
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
            # 更新状态栏日志文件显示
            self.status_log.setText("日志文件: 未创建")
            self.append_text("[系统]: 自动保存功能已关闭\n")
        
        # 保存配置
        self.save_config()


    
    def update_rts_dtr(self):
        """更新RTS和DTR状态"""
        if hasattr(self, 'serial_port') and self.serial_port and self.serial_port.is_open:
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
        if not hasattr(self, 'serial_port') or not self.serial_port or not self.serial_port.is_open:
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
        
        # 发送按钮（支持双击编辑）
        send_btn = QPushButton("无注释")
        send_btn.setFont(QFont("Microsoft YaHei", 8))
        send_btn.setMinimumWidth(70)  # 增加按钮宽度
        send_btn.clicked.connect(self.on_send_multi_btn_clicked)
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
            # 调整左侧大小
            self.main_splitter.setSizes([1000, 0])
        else:
            right_content.show()
            self.btn_toggle_multi_send.setText("隐藏多字符发送")
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
        
        5. 双击修改：
           - 双击"字符串"列可编辑对应行的内容
           - 双击"点击发送"按钮可修改按钮文本
        
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
        
        dialog.exec_()

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
            if not hasattr(self, 'serial_port') or not self.serial_port or not self.serial_port.is_open:
                QMessageBox.warning(self, "警告", "请先打开串口！")
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
                            if not hasattr(self.parent, 'serial_port') or not self.parent.serial_port or not self.parent.serial_port.is_open:
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

                # 发送按钮（支持双击编辑，恢复保存的按钮文本）
                button_text = item.get("button_text", "无注释")
                send_btn = QPushButton(button_text)
                send_btn.setFont(QFont("Microsoft YaHei", 8))
                send_btn.setMinimumWidth(70)
                send_btn.clicked.connect(self.on_send_multi_btn_clicked)
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

                # 顺序
                order_item = QTableWidgetItem(item.get("order", "1"))
                order_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                order_item.setTextAlignment(Qt.AlignCenter)
                self.table_multi_send.setItem(i, 4, order_item)

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
                self.statusBar().showMessage(error_msg)
                return

            # 写入数据（使用 try-except-else-finally 确保文件句柄安全）
            write_success = False
            try:
                self.current_log_file.write(data)
                self.current_log_file.flush()  # 立即刷新到磁盘
                # 强制写入磁盘
                os.fsync(self.current_log_file.fileno())
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
                
                # 加载串口设置
                if 'port' in config and isinstance(config['port'], str):
                    port_index = self.combo_port.findText(config['port'])
                    if port_index >= 0:
                        self.combo_port.setCurrentIndex(port_index)
                
                if 'baudrate' in config and isinstance(config['baudrate'], (int, str)):
                    baud_index = self.combo_baud.findText(str(config['baudrate']))
                    if baud_index >= 0:
                        self.combo_baud.setCurrentIndex(baud_index)
                
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
                    self.check_timestamp.setChecked(config['show_timestamp'])
                
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
                        button.setFont(QFont("Microsoft YaHei", 8))
                        button.setMinimumWidth(70)
                        button.clicked.connect(self.on_send_multi_btn_clicked)
                        # 为按钮设置唯一的对象名称，用于事件过滤器
                        button.setObjectName(f"btn_{row}")
                        # 安装事件过滤器以支持双击编辑
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
                        delay_spin.setFont(QFont("Consolas", 8))
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
            'head_field_enabled': self.check_head_field.isChecked(),
            'tail_field_enabled': self.check_tail_field.isChecked(),
            'multi_items': multi_items,
            # 更多串口设置参数
            'data_bits': getattr(self, 'serial_data_bits', '8'),
            'stop_bits': getattr(self, 'serial_stop_bits', '1'),
            'parity': getattr(self, 'serial_parity', 'None'),
            'flow_control': getattr(self, 'serial_flow_control', 'None'),
            # 编码格式
            'encoding': self.combo_encoding.currentText()
        }
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
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
