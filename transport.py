"""
统一传输层：TransportWrapper 封装 Serial / UDP / TCP Client / TCP Server，
TransportReadThread 在独立线程中轮询接收数据。
"""

import socket
import select

from PyQt5.QtCore import QThread, pyqtSignal, QMutex, QMutexLocker

import serial


class TransportWrapper:
    """统一传输层，封装 Serial / UDP / TCP Client / TCP Server"""
    def __init__(self):
        self.mode = 'serial'
        self._serial = None
        self._socket = None
        self._client_conn = None
        self._client_addr = None
        self._remote_addr = None

    @property
    def is_open(self):
        if self.mode == 'serial':
            return self._serial is not None and self._serial.is_open
        elif self.mode == 'tcp_server':
            return self._socket is not None
        else:
            return self._socket is not None

    @property
    def in_waiting(self):
        if self.mode == 'serial':
            return self._serial.in_waiting if self._serial else 0
        elif self.mode == 'tcp_server':
            if self._client_conn is not None:
                import select
                r, _, _ = select.select([self._client_conn], [], [], 0)
                return 4096 if r else 0  # recv 会自行截断到实际可用字节数
            # 检查服务端 socket 是否有待处理的新连接（否则 accept() 永远不会被调用）
            if self._socket is not None:
                import select
                r, _, _ = select.select([self._socket], [], [], 0)
                return 4096 if r else 0  # 触发 read() 调用，内部会先 accept() 再 recv()
            return 0
        else:
            if self._socket is not None:
                import select
                r, _, _ = select.select([self._socket], [], [], 0)
                return 1 if r else 0
            return 0

    def open_serial(self, port, baudrate, **kwargs):
        self.close()
        self.mode = 'serial'
        self._serial = serial.Serial(port=port, baudrate=baudrate, **kwargs)
        return True

    def open_udp(self, local_ip, local_port, remote_ip, remote_port):
        self.close()
        self.mode = 'udp'
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((local_ip, int(local_port)))
        self._socket.setblocking(False)
        self._remote_addr = (remote_ip, int(remote_port))
        return True

    def open_tcp_client(self, remote_ip, remote_port):
        self.close()
        self.mode = 'tcp_client'
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(5)
        self._socket.connect((remote_ip, int(remote_port)))
        self._socket.setblocking(False)
        return True

    def open_tcp_server(self, local_ip, local_port):
        self.close()
        self.mode = 'tcp_server'
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((local_ip, int(local_port)))
        self._socket.listen(1)
        self._socket.setblocking(False)
        return True

    def close(self):
        if self._serial is not None:
            try:
                if self._serial.is_open:
                    self._serial.close()
            except Exception:
                pass
            self._serial = None
        if self._client_conn is not None:
            try:
                self._client_conn.close()
            except Exception:
                pass
            self._client_conn = None
            self._client_addr = None
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def read(self, size=4096):
        if self.mode == 'serial':
            if self._serial is None:
                return b''
            try:
                return self._serial.read(size)
            except (serial.SerialTimeoutException, serial.SerialException):
                return b''
        elif self.mode == 'udp':
            if self._socket is None:
                return b''
            try:
                data, _addr = self._socket.recvfrom(size)
                return data
            except BlockingIOError:
                return b''
            except OSError:
                return b''
        elif self.mode == 'tcp_client':
            if self._socket is None:
                return b''
            try:
                data = self._socket.recv(size)
                if data:
                    return data
                self._socket.close()
                self._socket = None
                return b''
            except BlockingIOError:
                return b''
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                try:
                    self._socket.close()
                finally:
                    self._socket = None
                return b''
        elif self.mode == 'tcp_server':
            if self._socket is None:
                return b''
            try:
                conn, addr = self._socket.accept()
                if self._client_conn is not None:
                    try:
                        self._client_conn.close()
                    except Exception:
                        pass
                self._client_conn = conn
                self._client_conn.setblocking(False)
                self._client_addr = addr
            except BlockingIOError:
                pass
            except Exception:
                pass
            if self._client_conn is not None:
                try:
                    data = self._client_conn.recv(size)
                    if data:
                        return data
                    else:
                        self._client_conn.close()
                        self._client_conn = None
                        self._client_addr = None
                except BlockingIOError:
                    pass
                except (ConnectionResetError, ConnectionAbortedError, OSError):
                    try:
                        self._client_conn.close()
                    except OSError:
                        pass
                    self._client_conn = None
                    self._client_addr = None
            return b''
        return b''

    def write(self, data):
        if self.mode == 'serial':
            return self._serial.write(data) if self._serial else 0
        elif self.mode == 'udp':
            return self._socket.sendto(data, self._remote_addr) if self._socket else 0
        elif self.mode == 'tcp_client':
            if self._socket is None:
                return 0
            try:
                return self._send_socket_data(self._socket, data)
            except TimeoutError:
                raise
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                try:
                    self._socket.close()
                finally:
                    self._socket = None
                return 0
        elif self.mode == 'tcp_server':
            if self._client_conn is not None:
                try:
                    return self._send_socket_data(self._client_conn, data)
                except TimeoutError:
                    raise
                except (ConnectionResetError, ConnectionAbortedError, OSError):
                    try:
                        self._client_conn.close()
                    except OSError:
                        pass
                    self._client_conn = None
                    self._client_addr = None
            return 0
        return 0

    @staticmethod
    def _send_socket_data(sock, data, writable_timeout=1.0):
        """在 non-blocking TCP socket 上处理背压和部分写入。"""
        view = memoryview(data)
        total = 0
        while total < len(view):
            try:
                sent = sock.send(view[total:])
            except BlockingIOError:
                _, writable, _ = select.select([], [sock], [], writable_timeout)
                if not writable:
                    raise TimeoutError("TCP 发送等待可写超时")
                continue
            if sent <= 0:
                raise ConnectionResetError("TCP 连接已关闭")
            total += sent
        return total

    @property
    def rts(self):
        return self._serial.rts if self._serial else False

    @rts.setter
    def rts(self, value):
        if self._serial is not None:
            self._serial.rts = value

    @property
    def dtr(self):
        return self._serial.dtr if self._serial else False

    @dtr.setter
    def dtr(self, value):
        if self._serial is not None:
            self._serial.dtr = value

    def flush(self):
        if self._serial is not None:
            self._serial.flush()

    def reset_input_buffer(self):
        """清空串口输入缓冲区（网络模式下为空操作）"""
        if self.mode == 'serial' and self._serial is not None:
            self._serial.reset_input_buffer()

    def reset_output_buffer(self):
        """清空串口输出缓冲区（网络模式下为空操作）"""
        if self.mode == 'serial' and self._serial is not None:
            self._serial.reset_output_buffer()


class TransportReadThread(QThread):
    receive_data_signal = pyqtSignal(bytes)
    error_signal = pyqtSignal(str)

    def __init__(self, transport, transport_mutex):
        super().__init__()
        self.transport = transport
        self.running = True
        self.running_lock = QMutex()
        self.transport_mutex = transport_mutex

    def is_running(self):
        self.running_lock.lock()
        result = self.running
        self.running_lock.unlock()
        return result

    def set_running(self, value):
        self.running_lock.lock()
        self.running = value
        self.running_lock.unlock()

    def run(self):
        try:
            while self.is_running():
                try:
                    data_read = False
                    with QMutexLocker(self.transport_mutex):
                        if not self.transport or not self.transport.is_open:
                            self.error_signal.emit("连接已关闭")
                            self.set_running(False)
                            break
                        if self.transport.in_waiting > 0:
                            try:
                                data = self.transport.read(self.transport.in_waiting or 4096)
                                if data:
                                    self.receive_data_signal.emit(data)
                                    data_read = True
                            except Exception as e:
                                self.error_signal.emit(f"读取异常: {e}")
                                self.set_running(False)
                                break
                    self.msleep(1 if data_read else 20)
                except ValueError as e:
                    print(e); self.error_signal.emit(f"数据解析错误: {e}"); self.msleep(100)
                except TimeoutError as e:
                    print(e); self.error_signal.emit(f"超时错误: {e}"); self.msleep(100)
                except KeyboardInterrupt:
                    self.error_signal.emit("用户中断操作"); self.set_running(False); break
                except SystemExit:
                    self.error_signal.emit("系统退出"); self.set_running(False); break
                except MemoryError:
                    self.error_signal.emit("内存错误"); self.set_running(False); break
                except Exception as e:
                    print(e); self.error_signal.emit(f"读取错误: {e}"); self.msleep(100)
        finally:
            self.set_running(False)

    def stop(self):
        self.set_running(False)
        if not self.wait(2000):
            print(f"警告: 线程停止超时，可能卡在传输操作中")
