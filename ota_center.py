"""
OTA 升级控制中心：HTTP 服务管理、固件选择、OTA 流程控制及独立日志区。
内含 OTARequestHandler、_ThreadingHTTPServer、OTAServerThread 和 OTAControlCenter。
"""

import os
import re
import socket
import time
import json
import shutil
import datetime
import codecs
import urllib.parse
import functools
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QPushButton, QTextEdit, QCheckBox,
                             QMessageBox, QSpinBox, QLineEdit, QProgressBar,
                             QGroupBox, QFileDialog, QApplication)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QMutexLocker
from PyQt5.QtGui import QFont, QPainter, QPixmap, QColor


# ═══════════════════════════════════════════════════════════════
#  OTA HTTP 服务基础类
# ═══════════════════════════════════════════════════════════════

class OTARequestHandler(SimpleHTTPRequestHandler):
    """自定义 HTTP 请求处理器，跟踪固件文件下载进度。"""

    _lock = threading.Lock()
    _bytes_sent = 0
    _total_size = 0
    _active_file = ''

    @classmethod
    def setup_progress(cls, filename, total_size):
        """在 OTA 开始前由控制中心调用，设置预期文件和大小。"""
        with cls._lock:
            cls._active_file = filename
            cls._total_size = total_size
            cls._bytes_sent = 0

    @classmethod
    def get_progress(cls):
        """返回 (bytes_sent, total_size, filename) 或 None。"""
        with cls._lock:
            if cls._total_size > 0:
                return (cls._bytes_sent, cls._total_size, cls._active_file)
            return None

    def log_message(self, format, *args):
        """抑制默认 stderr 日志输出。"""
        pass

    def copyfile(self, source, outputfile):
        """覆写以统计已发送字节数，用于进度上报。

        只统计本次 OTA 目标文件（_active_file）的发送量，避免其他文件访问、
        残留连接或多客户端并发请求污染进度计数。
        """
        buf_size = 64 * 1024  # 64KB 块
        # 判断当前请求的文件是否是本次 OTA 的目标文件
        try:
            current_file = os.path.basename(self.translate_path(self.path))
        except Exception:
            current_file = ''
        with OTARequestHandler._lock:
            track = bool(OTARequestHandler._active_file
                         and current_file == OTARequestHandler._active_file)
        while True:
            buf = source.read(buf_size)
            if not buf:
                break
            outputfile.write(buf)
            if track:
                with OTARequestHandler._lock:
                    OTARequestHandler._bytes_sent += len(buf)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """支持多线程的 HTTP 服务器（daemon 线程，随主线程退出）。"""
    daemon_threads = True


class OTAServerThread(QThread):
    """在独立线程中运行 HTTP 文件服务器。"""
    server_started = pyqtSignal(int)   # port
    server_stopped = pyqtSignal()
    server_error = pyqtSignal(str)

    def __init__(self, serve_dir, port):
        super().__init__()
        self.serve_dir = os.path.abspath(serve_dir)
        self.port = port
        self.httpd = None

    def run(self):
        # 确保服务目录存在
        if not os.path.isdir(self.serve_dir):
            try:
                os.makedirs(self.serve_dir)
            except OSError as e:
                self.server_error.emit(f"无法创建服务目录: {e}")
                return

        # 使用 functools.partial 将 directory 传入 handler
        handler = functools.partial(OTARequestHandler, directory=self.serve_dir)

        try:
            self.httpd = _ThreadingHTTPServer(('0.0.0.0', self.port), handler)
            self.server_started.emit(self.port)
            self.httpd.serve_forever()
        except OSError as e:
            self.server_error.emit(f"端口 {self.port} 被占用或无法绑定: {e}")
        except Exception as e:
            self.server_error.emit(str(e))
        finally:
            self.server_stopped.emit()

    def stop(self):
        """安全停止 HTTP 服务器。"""
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            finally:
                self.httpd = None


# ═══════════════════════════════════════════════════════════════
#  OTA 升级控制中心
# ═══════════════════════════════════════════════════════════════


class OTAControlCenter(QDialog):
    """OTA 升级控制中心对话框。

    提供 HTTP 服务管理、固件选择、OTA 流程控制以及独立日志区。
    """

    # ── 状态常量 ──
    STATE_IDLE = 'idle'
    STATE_COPYING = 'copying'
    STATE_SENDING = 'sending_cmd'
    STATE_DOWNLOADING = 'downloading'
    STATE_SUCCESS = 'success'
    STATE_FAILED = 'failed'
    STATE_CANCELLED = 'cancelled'

    MAX_FIRMWARE_HISTORY = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent          # SerialTool 实例
        self.server_thread = None          # OTAServerThread
        self.http_running = False
        self.ota_state = self.STATE_IDLE
        self.firmware_path = ''
        self._last_ip = ''  # 上次使用的 IP，用于恢复
        self._ota_stop_flag = False
        self._ota_in_progress = False
        # 进度模式（兼容"上报进度"与"不上报进度"两类设备）：
        #   'waiting' —— 初始，等待设备上报 Progress 或 HTTP 开始发送
        #   'device'  —— 设备上报模式，进度条以设备 ota: Progress:XX% 为准
        #   'http'    —— HTTP 发送量回退模式（设备不上报进度），用服务器发送量近似
        self._progress_mode = 'waiting'

        # 进度跟踪
        self._progress_poll_timer = QTimer(self)
        self._progress_poll_timer.setInterval(500)
        self._progress_poll_timer.timeout.connect(self._poll_progress)
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_ota_timeout)
        # 回退定时器：HTTP 开始发送后等待设备上报 Progress 的宽限期，
        # 超时仍无上报则判定设备不上报进度，切换到 http 回退模式
        self._fallback_timer = QTimer(self)
        self._fallback_timer.setSingleShot(True)
        self._fallback_timer.setInterval(5000)  # 5 秒宽限
        self._fallback_timer.timeout.connect(self._on_fallback_timeout)
        self._serial_data_connected = False

        self.init_ui()
        self._load_settings()

    # ─────────────────────────────────────────────────────────────
    #  UI 构建
    # ─────────────────────────────────────────────────────────────

    def init_ui(self):
        self.setWindowTitle("OTA 升级控制中心")
        self.setMinimumSize(620, 660)  # 660px 在 1366×768 笔记本上不被任务栏遮挡
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── 1. HTTP 服务管理 ──
        http_group = QGroupBox("HTTP 服务管理")
        http_layout = QVBoxLayout(http_group)
        http_layout.setSpacing(6)

        # 状态行
        status_row = QHBoxLayout()
        self._indicator_label = QLabel()
        self._indicator_label.setFixedSize(18, 18)
        self._draw_indicator(False)
        status_row.addWidget(self._indicator_label)
        self._http_status_label = QLabel("已停止")
        status_row.addWidget(self._http_status_label)
        status_row.addStretch()
        http_layout.addLayout(status_row)

        # 端口行
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("端口:"))
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        self._port_spin.setValue(8080)
        self._port_spin.setToolTip("HTTP 服务端口（1024–65535），服务启动后不可修改")
        port_row.addWidget(self._port_spin)
        port_row.addStretch()
        http_layout.addLayout(port_row)

        # IP 行
        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("本机 IP:"))
        self._ip_combo = QComboBox()
        self._ip_combo.setMinimumWidth(180)
        self._ip_combo.setToolTip("选择用于 OTA 下载的本机局域网 IP 地址")
        ip_row.addWidget(self._ip_combo)
        self._btn_refresh_ip = QPushButton("刷新")
        self._btn_refresh_ip.setMinimumWidth(56)
        self._btn_refresh_ip.setFont(QFont("Microsoft YaHei", 9))
        self._btn_refresh_ip.setToolTip("刷新本机 IP 列表")
        self._btn_refresh_ip.clicked.connect(self._refresh_ips)
        ip_row.addWidget(self._btn_refresh_ip)
        ip_row.addStretch()
        http_layout.addLayout(ip_row)

        # 服务按钮行
        srv_btn_row = QHBoxLayout()
        self._btn_start_server = QPushButton("启动服务")
        self._btn_start_server.clicked.connect(self._start_http_service)
        srv_btn_row.addWidget(self._btn_start_server)
        self._btn_stop_server = QPushButton("停止服务")
        self._btn_stop_server.setEnabled(False)
        self._btn_stop_server.clicked.connect(self._stop_http_service)
        srv_btn_row.addWidget(self._btn_stop_server)
        srv_btn_row.addStretch()
        http_layout.addLayout(srv_btn_row)

        layout.addWidget(http_group)

        # ── 2. 固件选择 ──
        fw_group = QGroupBox("固件选择")
        fw_layout = QVBoxLayout(fw_group)
        fw_layout.setSpacing(6)

        path_row = QHBoxLayout()
        self._fw_path_edit = QLineEdit()
        self._fw_path_edit.setReadOnly(True)
        self._fw_path_edit.setPlaceholderText("请选择固件文件...")
        path_row.addWidget(self._fw_path_edit)
        btn_browse = QPushButton("浏览")
        btn_browse.clicked.connect(self._browse_firmware)
        path_row.addWidget(btn_browse)
        fw_layout.addLayout(path_row)

        extra_row = QHBoxLayout()
        btn_open_dir = QPushButton("打开服务目录")
        btn_open_dir.clicked.connect(self._open_serve_dir)
        extra_row.addWidget(btn_open_dir)
        extra_row.addWidget(QLabel("历史:"))
        self._history_combo = QComboBox()
        self._history_combo.setMinimumWidth(200)
        self._history_combo.setToolTip("最近使用的固件路径")
        self._history_combo.activated.connect(self._on_history_selected)
        extra_row.addWidget(self._history_combo, 1)
        fw_layout.addLayout(extra_row)

        layout.addWidget(fw_group)

        # ── 3. OTA 流程控制 ──
        ota_group = QGroupBox("OTA 流程控制")
        ota_layout = QVBoxLayout(ota_group)
        ota_layout.setSpacing(6)

        # 状态文本
        self._state_label = QLabel("就绪")
        self._state_label.setAlignment(Qt.AlignCenter)
        self._state_label.setFont(QFont("Microsoft YaHei", 10))
        ota_layout.addWidget(self._state_label)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%p%")
        ota_layout.addWidget(self._progress_bar)

        # 按钮行（等宽）
        ota_btn_row = QHBoxLayout()
        self._btn_start_ota = QPushButton("▶ 开始升级")
        self._btn_start_ota.setMinimumHeight(36)
        self._btn_start_ota.setMinimumWidth(160)
        self._btn_start_ota.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self._btn_start_ota.setStyleSheet(
            "QPushButton { background-color: #4EC972; color: #FFFFFF; font-weight: bold;"
            "  border: 1px solid #3EB862; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3EB862; }"
            "QPushButton:pressed { background-color: #2EA852; }"
            "QPushButton:disabled { background-color: #555555; color: #888888; border-color: #444444; }")
        self._btn_start_ota.clicked.connect(self._start_ota)
        ota_btn_row.addWidget(self._btn_start_ota)

        self._btn_stop_ota = QPushButton("■ 停止")
        self._btn_stop_ota.setMinimumHeight(36)
        self._btn_stop_ota.setMinimumWidth(160)
        self._btn_stop_ota.setEnabled(False)
        self._btn_stop_ota.setStyleSheet(
            "QPushButton { background-color: #E06C75; color: #FFFFFF; font-weight: bold;"
            "  border: 1px solid #D05C65; border-radius: 4px; }"
            "QPushButton:hover { background-color: #D05C65; }"
            "QPushButton:pressed { background-color: #C04C55; }"
            "QPushButton:disabled { background-color: #555555; color: #888888; border-color: #444444; }")
        self._btn_stop_ota.clicked.connect(self._stop_ota)
        ota_btn_row.addWidget(self._btn_stop_ota)
        ota_layout.addLayout(ota_btn_row)

        # 选项行
        opt_row = QHBoxLayout()
        self._check_auto_stop = QCheckBox("升级后自动停止服务")
        self._check_auto_stop.setChecked(True)
        self._check_auto_stop.setToolTip("升级成功或失败后自动关闭 HTTP 服务")
        opt_row.addWidget(self._check_auto_stop)

        opt_row.addWidget(QLabel("  超时(s):"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(10, 3600)
        self._timeout_spin.setValue(120)
        self._timeout_spin.setToolTip("OTA 超时时间，超时无响应则判定失败")
        opt_row.addWidget(self._timeout_spin)

        opt_row.addStretch()
        ota_layout.addLayout(opt_row)

        # 指令格式行
        cmd_row = QHBoxLayout()
        cmd_row.addWidget(QLabel("OTA指令:"))
        self._cmd_format_edit = QLineEdit()
        self._cmd_format_edit.setText("ota {url}\\r\\n")
        self._cmd_format_edit.setToolTip(
            "{url} 将被替换为下载地址。默认 \\r\\n 结尾，可自由编辑")
        # 编辑完成（失焦/回车）自动补 \r\n 并持久化其他设置（端口/超时等）；
        # 指令本身不持久化，每次打开恢复默认值
        self._cmd_format_edit.editingFinished.connect(self._on_cmd_format_edited)
        cmd_row.addWidget(self._cmd_format_edit)
        ota_layout.addLayout(cmd_row)

        layout.addWidget(ota_group)

        # ── 4. OTA 日志 ──
        log_group = QGroupBox("OTA 日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(4, 4, 4, 4)
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setFont(QFont("Consolas", 9))
        self._log_edit.setMinimumHeight(120)
        log_layout.addWidget(self._log_edit)

        log_btn_row = QHBoxLayout()
        log_btn_row.addStretch()
        btn_clear_log = QPushButton("清空日志")
        btn_clear_log.clicked.connect(self._log_edit.clear)
        log_btn_row.addWidget(btn_clear_log)
        log_layout.addLayout(log_btn_row)
        layout.addWidget(log_group)

        # 应用主题
        if self.main_window and hasattr(self.main_window, '_apply_dialog_theme'):
            self.main_window._apply_dialog_theme(self)

    # ─────────────────────────────────────────────────────────────
    #  指示灯绘制
    # ─────────────────────────────────────────────────────────────

    def _draw_indicator(self, active):
        """绘制圆形状态指示灯（绿色=运行，灰色=停止）。"""
        pix = QPixmap(18, 18)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(0x4E, 0xC9, 0x72) if active else QColor(0x80, 0x80, 0x80)
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, 14, 14)
        p.end()
        self._indicator_label.setPixmap(pix)

    # ─────────────────────────────────────────────────────────────
    #  网络工具
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _get_local_ips():
        """获取本机局域网 IPv4 地址列表。"""
        ips = []
        try:
            hostname = socket.gethostname()
            addrs = socket.getaddrinfo(hostname, None, socket.AF_INET,
                                       socket.SOCK_STREAM, socket.IPPROTO_TCP)
            for addr in addrs:
                ip = addr[4][0]
                if ip != '127.0.0.1' and not ip.startswith('169.254'):
                    ips.append(ip)
        except Exception:
            pass

        # 补充：通过连接外网的方式获取 IP（不实际发包）
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and ip not in ips and ip != '127.0.0.1':
                ips.insert(0, ip)
        except Exception:
            pass

        return list(dict.fromkeys(ips))  # 去重保序

    @staticmethod
    def _is_port_available(port):
        """检测端口是否可用（True = 空闲）。"""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.bind(('0.0.0.0', port))
            s.close()
            return True
        except OSError:
            return False
        finally:
            try:
                s.close()
            except Exception:
                pass

    def _refresh_ips(self):
        """刷新本机 IP 下拉列表。"""
        current = self._ip_combo.currentText()
        ips = self._get_local_ips()
        self._ip_combo.clear()
        if ips:
            self._ip_combo.addItems(ips)
            if current in ips:
                self._ip_combo.setCurrentText(current)
            else:
                self._ip_combo.setCurrentIndex(0)
        else:
            self._ip_combo.addItem("无法获取 IP")

    def showEvent(self, event):
        """对话框显示时同步主题、刷新 IP、恢复上次状态。"""
        # 同步主窗口主题（支持运行时切换）
        if self.main_window and hasattr(self.main_window, '_apply_dialog_theme'):
            self.main_window._apply_dialog_theme(self)
        self._refresh_ips()
        # OTA 指令每次打开恢复默认值（用户可临时修改用于本次升级，不持久化）
        self._cmd_format_edit.setText("ota {url}\\r\\n")
        # 恢复上次选择的 IP
        if self._last_ip:
            idx = self._ip_combo.findText(self._last_ip)
            if idx >= 0:
                self._ip_combo.setCurrentIndex(idx)
        # 恢复上次选择的固件路径
        if not self.firmware_path and self._history_combo.count() > 0:
            last_path = self._history_full_path(0)
            if last_path and os.path.exists(last_path):
                self.firmware_path = last_path
                self._fw_path_edit.setText(last_path)
                self._history_combo.setCurrentIndex(0)
        super().showEvent(event)

    @property
    def _serve_dir(self):
        """ota_serve 服务根目录（位于程序工作目录下）。"""
        return os.path.join(os.getcwd(), 'ota_serve')

    # ─────────────────────────────────────────────────────────────
    #  设置持久化
    # ─────────────────────────────────────────────────────────────

    def _load_settings(self):
        """从主窗口配置中加载 OTA 相关设置。"""
        config = {}
        config_file = os.path.join(os.getcwd(), 'serial_config.json')
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except Exception:
                pass

        ota = config.get('ota', {}) if isinstance(config, dict) else {}

        port = ota.get('port', 8080)
        if isinstance(port, int) and 1024 <= port <= 65535:
            self._port_spin.setValue(port)

        auto_stop = ota.get('auto_stop', True)
        self._check_auto_stop.setChecked(bool(auto_stop))

        timeout = ota.get('timeout', 120)
        if isinstance(timeout, int):
            self._timeout_spin.setValue(max(10, min(timeout, 3600)))

        # 注意：cmd_format 不从配置加载——showEvent 每次打开都恢复默认值，
        # 用户可临时修改用于本次升级，不持久化。

        last_ip = ota.get('last_ip', '')
        if isinstance(last_ip, str):
            self._last_ip = last_ip.strip()

        history = ota.get('firmware_history', [])
        if isinstance(history, list):
            for path in history:
                if isinstance(path, str) and path.strip():
                    self._insert_history(path.strip(), at_front=False)

    def _on_cmd_format_edited(self):
        """OTA 指令编辑完成回调：持久化其他设置（端口/超时等）。

        指令本身不持久化——每次打开 OTA 控制中心都恢复默认值，
        用户可临时修改用于本次升级。不自动追加 \\r\\n，完全尊重用户输入。
        """
        self._save_settings()

    def _save_settings(self):
        """将 OTA 相关设置写回主窗口配置文件。"""
        config_file = os.path.join(os.getcwd(), 'serial_config.json')
        config = {}
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except Exception:
                pass
        if not isinstance(config, dict):
            config = {}

        # 收集固件历史
        history = []
        for i in range(self._history_combo.count()):
            path = self._history_full_path(i)
            if path.strip():
                history.append(path.strip())

        config['ota'] = {
            'port': self._port_spin.value(),
            'auto_stop': self._check_auto_stop.isChecked(),
            'timeout': self._timeout_spin.value(),
            # OTA 指令不持久化用户修改，始终保存默认值
            'cmd_format': 'ota {url}\\r\\n',
            'firmware_history': history[:self.MAX_FIRMWARE_HISTORY],
            'last_ip': self._ip_combo.currentText(),
        }

        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────
    #  OTA 日志
    # ─────────────────────────────────────────────────────────────

    def _log(self, message):
        """向 OTA 日志区追加一条带时间戳的消息。"""
        ts = datetime.datetime.now().strftime("[%H:%M:%S] ")
        self._log_edit.append(ts + message)

    # ─────────────────────────────────────────────────────────────
    #  HTTP 服务管理
    # ─────────────────────────────────────────────────────────────

    def _start_http_service(self):
        """启动 HTTP 文件服务器。"""
        port = self._port_spin.value()

        # 检测端口
        if not self._is_port_available(port):
            QMessageBox.warning(self, "端口占用",
                                f"端口 {port} 已被占用，请更换其他端口。")
            return

        # 确保 ota_serve 目录存在
        try:
            os.makedirs(self._serve_dir, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "错误", f"无法创建服务目录: {e}")
            return

        # 刷新 IP 列表
        self._refresh_ips()

        # 启动服务线程
        self.server_thread = OTAServerThread(self._serve_dir, port)
        self.server_thread.server_started.connect(self._on_server_started)
        self.server_thread.server_stopped.connect(self._on_server_stopped)
        self.server_thread.server_error.connect(self._on_server_error)
        self.server_thread.start()

    def _on_server_started(self, port):
        """HTTP 服务启动成功回调。"""
        self.http_running = True
        self._draw_indicator(True)
        self._http_status_label.setText("已启动")
        self._btn_start_server.setEnabled(False)
        self._btn_stop_server.setEnabled(True)
        self._port_spin.setEnabled(False)
        ip = self._ip_combo.currentText()
        self._log(f"HTTP 服务已启动，端口 {port}，根目录 {self._serve_dir}")
        self._log(f"本机 IP: {ip}")
        self._save_settings()

    def _on_server_stopped(self):
        """HTTP 服务停止回调。"""
        self.http_running = False
        self._draw_indicator(False)
        self._http_status_label.setText("已停止")
        self._btn_start_server.setEnabled(True)
        self._btn_stop_server.setEnabled(False)
        self._port_spin.setEnabled(True)
        self.server_thread = None
        self._log("HTTP 服务已停止")

    def _on_server_error(self, err_msg):
        """HTTP 服务错误回调。"""
        self._draw_indicator(False)
        self._http_status_label.setText("已停止")
        self._btn_start_server.setEnabled(True)
        self._btn_stop_server.setEnabled(False)
        self._port_spin.setEnabled(True)
        self.http_running = False
        if self.server_thread:
            self.server_thread.stop()
            self.server_thread = None
        self._log(f"HTTP 服务错误: {err_msg}")
        QMessageBox.critical(self, "服务错误", f"HTTP 服务启动失败:\n{err_msg}")

    def _stop_http_service(self):
        """停止 HTTP 文件服务器。"""
        if self.server_thread:
            self.server_thread.stop()
            # _on_server_stopped 会由 server_stopped 信号触发

    # ─────────────────────────────────────────────────────────────
    #  固件选择
    # ─────────────────────────────────────────────────────────────

    def _browse_firmware(self):
        """打开文件对话框选择固件。"""
        filters = (
            "固件文件 (*.bin *.hex *.fw *.img *.ota *.dfu *.hex2 *.zip);;"
            "二进制文件 (*.bin);;"
            "HEX 文件 (*.hex);;"
            "所有文件 (*.*)"
        )
        path, _ = QFileDialog.getOpenFileName(self, "选择固件文件", "", filters)
        if path:
            self._set_firmware_path(path)

    def _format_history_label(self, path):
        """历史下拉框显示文本：用文件名显示，便于在众多记录中辨识版本。"""
        name = os.path.basename(path)
        return name if name else path

    def _history_full_path(self, index):
        """取历史项的完整路径（userData）；兼容旧数据回退到 itemText。"""
        if index < 0:
            return ''
        data = self._history_combo.itemData(index)
        if isinstance(data, str) and data:
            return data
        return self._history_combo.itemText(index)

    def _insert_history(self, path, at_front=False):
        """插入一条历史（按完整路径去重）：显示文件名、userData 存完整路径、tooltip 显示完整路径。"""
        self._history_combo.blockSignals(True)
        try:
            for i in range(self._history_combo.count()):
                if self._history_full_path(i) == path:
                    self._history_combo.removeItem(i)
                    break
            label = self._format_history_label(path)
            if at_front:
                self._history_combo.insertItem(0, label, path)
                idx = 0
            else:
                self._history_combo.addItem(label, path)
                idx = self._history_combo.count() - 1
            self._history_combo.setItemData(idx, path, Qt.ToolTipRole)
            while self._history_combo.count() > self.MAX_FIRMWARE_HISTORY:
                self._history_combo.removeItem(self._history_combo.count() - 1)
        finally:
            self._history_combo.blockSignals(False)

    def _set_firmware_path(self, path):
        """设置固件路径并更新历史记录。"""
        self.firmware_path = path
        self._fw_path_edit.setText(path)
        self._log(f"已选择固件: {path}")

        # 更新历史（置顶去重，阻断信号避免触发 activated 回调）
        self._insert_history(path, at_front=True)
        self._history_combo.setCurrentIndex(0)

        self._save_settings()

    def _on_history_selected(self, index):
        """历史下拉框用户选择回调（activated 信号传索引）：同步选中对应固件。"""
        path = self._history_full_path(index)
        if not path:
            return
        if os.path.exists(path):
            self.firmware_path = path
            self._fw_path_edit.setText(path)
            self._log(f"已从历史选择固件: {path}")
            self._save_settings()
        else:
            # 文件已不存在，提示并从历史中移除
            QMessageBox.information(self, "提示",
                                    f"固件文件已不存在，已从历史中移除:\n{path}")
            self._history_combo.blockSignals(True)
            self._history_combo.removeItem(index)
            self._history_combo.blockSignals(False)
            self._fw_path_edit.clear()
            self.firmware_path = ''
            self._save_settings()

    def _open_serve_dir(self):
        """在文件管理器中打开 ota_serve 目录。"""
        try:
            os.makedirs(self._serve_dir, exist_ok=True)
        except OSError:
            pass
        try:
            os.startfile(self._serve_dir)
        except Exception:
            QMessageBox.warning(self, "提示",
                                f"服务目录: {self._serve_dir}")

    @staticmethod
    def _decode_escapes(s):
        """将字面转义序列解码为控制字符：\\r→CR, \\n→LF, \\t→TAB, \\\\→\\。"""
        try:
            return codecs.decode(s, 'unicode_escape')
        except Exception:
            return s

    # ─────────────────────────────────────────────────────────────
    #  OTA 流程控制 —— 状态机
    # ─────────────────────────────────────────────────────────────

    def _set_state(self, state, message):
        """更新 OTA 状态并刷新 UI。"""
        self.ota_state = state
        self._state_label.setText(message)

        running_states = {self.STATE_COPYING, self.STATE_SENDING,
                          self.STATE_DOWNLOADING}
        is_running = state in running_states

        # 停止按钮仅在 OTA 执行中可用
        self._btn_stop_ota.setEnabled(is_running)

        # 进度条颜色
        if state == self.STATE_SUCCESS:
            self._progress_bar.setStyleSheet(
                "QProgressBar::chunk { background-color: #4EC972; }")
        elif state in (self.STATE_FAILED, self.STATE_CANCELLED):
            self._progress_bar.setStyleSheet(
                "QProgressBar::chunk { background-color: #E06C75; }")
        elif is_running:
            self._progress_bar.setStyleSheet(
                "QProgressBar::chunk { background-color: #528BFF; }")
        else:
            self._progress_bar.setStyleSheet(
                "QProgressBar::chunk { background-color: #528BFF; }")

        # 状态文字着色
        if state == self.STATE_SUCCESS:
            self._state_label.setStyleSheet("color: #4EC972; font-weight: bold;")
        elif state in (self.STATE_FAILED, self.STATE_CANCELLED):
            self._state_label.setStyleSheet("color: #E06C75; font-weight: bold;")
        elif is_running:
            self._state_label.setStyleSheet("color: #528BFF; font-weight: bold;")
        else:
            self._state_label.setStyleSheet("")

    def _copy_firmware_safely(self, src_path, dest_dir):
        """安全地把固件复制到 HTTP 服务目录，规避 Windows 文件占用问题。

        方案 A：若源文件已在服务目录内，直接返回该路径，跳过复制（避免自复制冲突）。
        方案 B：复制到临时文件 + 失败重试 + 原子替换，应对 HTTP 服务器/杀毒软件
                短暂占用目标文件导致的 WinError 32（ERROR_SHARING_VIOLATION）。

        返回 (dest_path, dst_size)；失败抛 IOError。
        """
        src_path = os.path.abspath(src_path)
        dest_dir = os.path.abspath(dest_dir)
        fw_filename = os.path.basename(src_path)
        dest_path = os.path.join(dest_dir, fw_filename)

        # ── 方案 A：源文件已在服务目录内，无需复制 ──
        if os.path.normcase(src_path) == os.path.normcase(dest_path):
            self._log(f"固件已在服务目录内，跳过复制: {fw_filename}")
            return dest_path, os.path.getsize(dest_path)

        # 确保服务目录存在
        os.makedirs(dest_dir, exist_ok=True)

        # ── 方案 B：复制到临时文件 + 重试 + 原子替换 ──
        # 临时文件名加进程/时间后缀，避免多实例并发时碰撞
        tmp_suffix = f".ota_tmp_{os.getpid()}_{int(time.time() * 1000)}"
        tmp_path = dest_path + tmp_suffix

        max_retries = 5
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                # 复制到临时文件（不触碰目标文件，避免与正在读取的句柄冲突）
                shutil.copy2(src_path, tmp_path)
                # 校验临时文件大小
                src_size = os.path.getsize(src_path)
                tmp_size = os.path.getsize(tmp_path)
                if src_size != tmp_size:
                    raise IOError(f"临时文件复制校验失败: {src_size} vs {tmp_size}")

                # 原子替换目标文件
                # Windows 上 os.replace 可原子覆盖已存在的目标文件，
                # 且即使目标正被读取也能成功（替换的是目录项，不影响已打开的旧 inode）
                try:
                    os.replace(tmp_path, dest_path)
                except OSError as e:
                    # 若替换仍失败（极少见，目标被独占写锁定），退化为先删后改名
                    last_err = e
                    try:
                        if os.path.exists(dest_path):
                            os.remove(dest_path)
                        os.rename(tmp_path, dest_path)
                    except OSError as e2:
                        last_err = e2
                        raise
                # 成功
                return dest_path, os.path.getsize(dest_path)

            except (OSError, IOError) as e:
                last_err = e
                # 清理本次失败的临时文件
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
                # 仅对共享冲突/占用类错误重试；其他错误（如磁盘满）直接抛出
                is_sharing_violation = (
                    getattr(e, 'winerror', None) == 32  # ERROR_SHARING_VIOLATION
                    or getattr(e, 'errno', None) in (13,)  # PermissionError
                )
                if not is_sharing_violation or attempt == max_retries:
                    raise IOError(
                        f"固件复制失败（第 {attempt}/{max_retries} 次）: {e}"
                    ) from e
                # 退避后重试（200ms / 400ms / 600ms / 800ms）
                backoff = 0.2 * attempt
                self._log(f"目标文件被占用，{backoff:.1f}s 后重试 "
                          f"({attempt}/{max_retries})...")
                # 用 processEvents 保持 UI 响应，同时支持停止中断
                waited = 0.0
                while waited < backoff:
                    QApplication.processEvents()
                    if self._ota_stop_flag:
                        raise IOError("用户取消，固件复制已中断")
                    time.sleep(0.05)
                    waited += 0.05

        # 理论上不会到达
        raise IOError(f"固件复制失败: {last_err}")

    def _start_ota(self):
        """一键启动 OTA 流程。"""
        if self._ota_in_progress:
            self._log("OTA 正在执行中，请等待完成或点击停止")
            return
        self._ota_in_progress = True
        self._ota_stop_flag = False  # 清除上一次的取消标志（必须在 HTTP 等待循环前）

        # ── 前置检查 ──
        if not self.firmware_path or not os.path.exists(self.firmware_path):
            self._ota_in_progress = False
            QMessageBox.warning(self, "提示", "请先选择固件文件。")
            return

        if not (self.main_window and hasattr(self.main_window, 'transport')
                and self.main_window.transport
                and self.main_window.transport.is_open):
            self._ota_in_progress = False
            QMessageBox.warning(self, "提示", "请先打开连接。")
            return

        ip = self._ip_combo.currentText()
        if not ip or '.' not in ip:
            self._ota_in_progress = False
            QMessageBox.warning(self, "提示", "请先选择有效的本机 IP 地址。")
            return

        if not self.http_running:
            self._log("HTTP 服务未启动，正在自动启动...")
            self._start_http_service()
            # 等待服务启动（最多 3 秒，可被停止按钮中断）
            waited = 0
            while not self.http_running and waited < 30:
                QApplication.processEvents()
                time.sleep(0.1)
                waited += 1
                if self._ota_stop_flag:
                    self._log("用户取消，HTTP 服务启动等待已中断")
                    self._ota_in_progress = False
                    return
            if not self.http_running:
                self._ota_in_progress = False
                QMessageBox.critical(self, "错误",
                                     "HTTP 服务启动失败，无法进行 OTA 升级。")
                return

        # ── 开始 OTA 流程 ──
        self._progress_mode = 'waiting'  # 重置进度模式，等待设备上报或回退
        self._fallback_timer.stop()
        self._progress_bar.setValue(0)

        try:
            # Step 1: 复制固件（安全复制：跳过自复制 + 临时文件 + 重试 + 原子替换）
            self._set_state(self.STATE_COPYING, "正在复制固件...")
            self._log("正在复制固件到服务目录...")
            fw_filename = os.path.basename(self.firmware_path)
            dest_path, dst_size = self._copy_firmware_safely(
                self.firmware_path, self._serve_dir)

            self._log(f"固件已复制: {fw_filename} ({dst_size} 字节)")

            # Step 2: 生成 URL
            ip = self._ip_combo.currentText()
            port = self._port_spin.value()
            url = f"http://{ip}:{port}/{urllib.parse.quote(fw_filename)}"
            self._log(f"下载 URL: {url}")

            # Step 3: 发送 OTA 指令
            self._set_state(self.STATE_SENDING, "正在发送 OTA 指令...")
            cmd_template = self._cmd_format_edit.text()
            cmd = cmd_template.replace('{url}', url)

            # 如果指令不含 {url} 占位符，在末尾追加 URL
            if '{url}' not in cmd_template and url not in cmd:
                if not cmd.endswith('\n'):
                    cmd += '\n'
                # 默认行为：指令 + URL
                pass

            # 解码转义序列（\r \n \t \\）为实际控制字符
            cmd = self._decode_escapes(cmd)
            cmd_bytes = cmd.encode('utf-8')

            # 通过串口发送（清空输入缓冲，防止残留数据干扰）
            try:
                with QMutexLocker(self.main_window.serial_mutex):
                    self.main_window.transport.reset_input_buffer()
                    self.main_window.transport.reset_output_buffer()
                    n = self.main_window.transport.write(cmd_bytes)
                    self.main_window.transport.flush()
                    self.main_window.tx_bytes += n
                    self.main_window.label_tx_bytes.setText(
                        f"发送字节: {self.main_window.tx_bytes}")
                # 同步到主窗口日志，方便和手动发送对比
                self.main_window.append_text(f"[发送]: {cmd.strip()}\n")
                self.main_window.append_text(f"[系统]: 已发送 {n} 字节\n")
            except Exception as e:
                raise IOError(f"串口发送失败: {e}")

            self._log(f"已发送 OTA 指令 ({n} 字节)")

            # Step 4: 设备可能重启，暂时屏蔽串口错误弹窗
            self._suppress_serial_errors(True)

            # Step 5: 启动进度跟踪
            self._set_state(self.STATE_DOWNLOADING, "等待设备下载（可能重启）...")
            self._log("等待设备下载固件（设备可能重启，串口暂时断开属正常现象）...")

            # 同时启动 HTTP 进度轮询和串口上报监听
            self._start_http_tracking(fw_filename, dst_size)
            self._start_serial_tracking()

        except Exception as e:
            self._log(f"OTA 流程错误: {e}")
            self._set_state(self.STATE_FAILED, f"错误: {e}")
            self._progress_bar.setValue(0)
            self._suppress_serial_errors(False)
            self._on_ota_finished()
            QMessageBox.critical(self, "OTA 失败", str(e))

    def _start_http_tracking(self, filename, total_size):
        """HTTP 进度模式：定时轮询服务器已发送字节。"""
        OTARequestHandler.setup_progress(filename, total_size)
        self._progress_poll_timer.start()
        self._timeout_timer.start(self._timeout_spin.value() * 1000)

    def _start_serial_tracking(self):
        """监听串口数据，解析设备主动上报的进度/完成消息。"""
        self._serial_data_connected = True
        if self.main_window and hasattr(self.main_window, 'read_thread') and \
                self.main_window.read_thread:
            self.main_window.read_thread.receive_data_signal.connect(
                self._on_serial_progress)

    def _poll_progress(self):
        """HTTP 模式：轮询服务器发送量，按进度模式驱动进度条。

        - waiting：等待设备上报 Progress；HTTP 一旦开始发送(sent>0)启动回退定时器，
                   宽限期内进度条保持 0%，避免 begin 阶段造成虚假进度。
        - device：进度条由 _on_serial_progress 驱动，此处不覆盖；服务器发送完毕
                  时停止轮询并提示。
        - http：设备不上报进度，用服务器发送量近似（封顶 99%，留 1% 给写入完成）；
                发送完毕后停止轮询，等待设备串口确认或总超时。
        """
        progress = OTARequestHandler.get_progress()
        if not progress:
            return

        sent, total, _ = progress
        if total <= 0:
            return

        # ── device 模式：进度条以设备上报为准，此处仅处理发送完毕 ──
        if self._progress_mode == 'device':
            if sent >= total:
                self._progress_poll_timer.stop()
                self._log(f"固件已全部发送至网络 ({total // 1024}KB)，"
                          f"等待设备写入完成...")
            return

        # ── http 模式：用服务器发送量近似进度 ──
        if self._progress_mode == 'http':
            if sent >= total:
                # 发送完毕：封顶 99%，留 1% 给设备写入完成；停止轮询（发送量不再变化）
                self._progress_bar.setValue(99)
                self._state_label.setText(
                    f"固件传输完毕，等待设备写入... ({total // 1024}KB)")
                self._progress_poll_timer.stop()
                self._log(f"固件已全部发送 ({total // 1024}KB)，"
                          f"等待设备写入完成（设备不上报进度）...")
                return
            pct = min(int(sent / total * 99), 99)
            self._progress_bar.setValue(pct)
            self._state_label.setText(
                f"传输中 {pct}% ({sent // 1024}KB / {total // 1024}KB)")
            return

        # ── waiting 模式：等待设备上报或回退 ──
        if sent == 0:
            self._state_label.setText("等待设备开始下载...")
            return

        # 首次检测到 HTTP 开始发送：启动回退定时器（仅启动一次）
        if not self._fallback_timer.isActive():
            self._fallback_timer.start()
            self._log("检测到设备开始下载，等待设备上报进度"
                      "（若不上报将切换为传输进度模式）...")
        self._state_label.setText(
            f"等待设备上报进度… ({sent // 1024}KB / {total // 1024}KB)")

    def _on_fallback_timeout(self):
        """回退定时器触发：宽限期内未收到设备 Progress，判定设备不上报进度。

        切换到 http 回退模式，用 HTTP 服务器发送量近似驱动进度条，
        避免"设备不上报进度"时进度条卡在 0% 导致无法升级的体验问题。
        """
        if self._progress_mode != 'waiting':
            return
        self._progress_mode = 'http'
        self._log("设备未上报进度，切换为传输进度模式（按服务器发送量近似）")
        # 立即触发一次轮询，让进度条马上反映当前发送量
        self._poll_progress()

    def _on_serial_progress(self, data):
        """解析串口上报的进度/完成消息（匹配 ESP-IDF 日志格式）。"""
        if not self._serial_data_connected:
            return
        try:
            text = data.decode('utf-8', errors='replace')
        except Exception:
            text = data.decode('latin-1', errors='replace')

        # 进度: ota: Progress:94% 或 ota: Progress: 94%
        m = re.search(r'ota\s*:\s*Progress\s*:\s*(\d+)\s*%', text, re.IGNORECASE)
        if m:
            pct = int(m.group(1))
            pct = max(0, min(pct, 100))
            # 收到设备真实下载进度：取消回退定时器，进入设备上报模式
            if self._progress_mode != 'device':
                self._fallback_timer.stop()
                self._progress_mode = 'device'
                self._log("设备已上报进度，切换为设备上报模式")
            self._progress_bar.setValue(pct)
            self._state_label.setText(f"设备下载进度 {pct}%")
            self._log(f"设备上报进度: {pct}%")
            # 重置超时（设备还在上报，说明活着）
            self._timeout_timer.start(self._timeout_spin.value() * 1000)
            return

        # 成功: ota: upgrades res:0
        if re.search(r'ota\s*:\s*upgrades?\s*res\s*:\s*0\b', text, re.IGNORECASE):
            self._serial_data_connected = False
            self._disconnect_serial_signal()
            self._progress_poll_timer.stop()
            self._timeout_timer.stop()
            self._progress_bar.setValue(100)
            self._set_state(self.STATE_SUCCESS, "✅ 升级成功")
            self._log(f"设备回复成功: {text.strip()}")
            self._log("OTA 升级成功")
            self._on_ota_finished()
            return

        # 失败: ota: upgrades res:非0 或通用 ERROR/FAIL
        if re.search(r'ota\s*:\s*upgrades?\s*res\s*:\s*[1-9]', text, re.IGNORECASE) or \
           re.search(r'\+ERROR\b|OTA\s*FAIL|OTA\s*ERROR', text, re.IGNORECASE):
            self._serial_data_connected = False
            self._disconnect_serial_signal()
            self._progress_poll_timer.stop()
            self._timeout_timer.stop()
            self._set_state(self.STATE_FAILED, "设备上报错误")
            self._log(f"设备回复错误: {text.strip()}")
            self._on_ota_finished()
            QMessageBox.warning(self, "OTA 失败", f"设备返回错误:\n{text.strip()}")

    def _on_ota_timeout(self):
        """OTA 超时处理。"""
        # 先检查 HTTP 进度
        progress = OTARequestHandler.get_progress()
        if progress:
            sent, total, _ = progress
            if sent < total:
                self._log("OTA 超时: 下载未完成")
                self._set_state(self.STATE_FAILED, "OTA 超时")
                self._progress_poll_timer.stop()
                self._on_ota_finished()
                QMessageBox.warning(self, "OTA 超时",
                                    "固件下载超时，设备无响应。")
                return
            else:
                # 已传完但设备没确认
                self._log("固件已全部发送，但设备未确认（超时）")
                self._progress_poll_timer.stop()
                # device 模式：保留设备最后上报的进度，不强制 100%
                # waiting/http 模式：设备不上报完成，传输已完成，视为成功并置 100%
                if self._progress_mode != 'device':
                    self._progress_bar.setValue(100)
                self._set_state(self.STATE_SUCCESS, "✅ 升级完成（未收到确认）")
                self._on_ota_finished()
                return

        # 无 HTTP 进度数据，判定超时
        self._log("OTA 超时: 设备无响应")
        self._set_state(self.STATE_FAILED, "OTA 超时")
        self._disconnect_serial_signal()
        self._progress_poll_timer.stop()
        self._on_ota_finished()
        QMessageBox.warning(self, "OTA 超时", "等待设备响应超时。")

    def _on_ota_finished(self):
        """OTA 流程结束（成功/失败/取消）后的统一清理。"""
        self._ota_in_progress = False
        # 注意：不在此重置 _ota_stop_flag。
        # _start_ota 的 HTTP 启动等待循环通过 processEvents() 响应 UI，
        # 若此处重置为 False，用户点"停止"后 _stop_ota 设置的 True 会被覆盖，
        # 导致等待循环检测不到取消、OTA 继续执行。
        # _ota_stop_flag 由 _start_ota 开头重置为 False。
        self._progress_poll_timer.stop()
        self._timeout_timer.stop()
        self._fallback_timer.stop()
        self._disconnect_serial_signal()
        self._suppress_serial_errors(False)  # 恢复串口错误信号

        # 自动停止服务
        if self._check_auto_stop.isChecked() and self.http_running:
            self._log("正在自动停止 HTTP 服务...")
            self._stop_http_service()

    def _stop_ota(self):
        """紧急中断 OTA 流程。"""
        self._log("用户取消 OTA 流程")
        self._ota_stop_flag = True
        self._fallback_timer.stop()

        # 尝试发送取消指令
        if self.main_window and hasattr(self.main_window, 'transport') \
                and self.main_window.transport \
                and self.main_window.transport.is_open:
            try:
                cancel_cmd = "OTA:CANCEL\n"
                with QMutexLocker(self.main_window.serial_mutex):
                    self.main_window.transport.write(cancel_cmd.encode('utf-8'))
                self._log("已发送取消指令: OTA:CANCEL")
            except Exception:
                pass

        self._progress_poll_timer.stop()
        self._timeout_timer.stop()
        self._disconnect_serial_signal()
        self._set_state(self.STATE_CANCELLED, "升级已取消")
        self._on_ota_finished()

    def _suppress_serial_errors(self, suppress):
        """OTA 期间屏蔽/恢复串口错误信号，防止设备重启导致的错误弹窗。"""
        if not (self.main_window and hasattr(self.main_window, 'read_thread')
                and self.main_window.read_thread):
            return
        try:
            if suppress:
                self.main_window.read_thread.error_signal.disconnect(
                    self.main_window.handle_read_error)
            else:
                self.main_window.read_thread.error_signal.connect(
                    self.main_window.handle_read_error)
        except (TypeError, RuntimeError):
            pass  # 已断开或已连接

    def _disconnect_serial_signal(self):
        """断开串口接收信号的连接。"""
        if self._serial_data_connected and self.main_window and \
                hasattr(self.main_window, 'read_thread') and \
                self.main_window.read_thread:
            try:
                self.main_window.read_thread.receive_data_signal.disconnect(
                    self._on_serial_progress)
            except (TypeError, RuntimeError):
                pass
        self._serial_data_connected = False

    # ─────────────────────────────────────────────────────────────
    #  拖放支持
    # ─────────────────────────────────────────────────────────────

    _FW_EXTENSIONS = {'.bin', '.hex', '.fw', '.img', '.ota', '.dfu', '.hex2', '.zip', '.gz', '.tar', '.elf', '.axf'}

    def dragEnterEvent(self, event):
        """拖入时检查是否为固件文件。"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                if ext in self._FW_EXTENSIONS or os.path.isdir(path):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        """放下文件时设置固件路径。"""
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path):
                ext = os.path.splitext(path)[1].lower()
                if ext in self._FW_EXTENSIONS:
                    self._set_firmware_path(path)
                    return
        event.ignore()

    # ─────────────────────────────────────────────────────────────
    #  生命周期
    # ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        """关闭对话框时保存设置并清理（HTTP 服务继续运行）。"""
        self._save_settings()
        self._progress_poll_timer.stop()
        self._timeout_timer.stop()
        self._fallback_timer.stop()
        self._disconnect_serial_signal()
        # HTTP 服务继续运行，不受对话框关闭影响
        event.accept()

    def reject(self):
        """Esc / 关闭按钮回调。"""
        self.close()
