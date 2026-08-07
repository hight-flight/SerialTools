import socket
import unittest
from unittest import mock

from transport import TransportWrapper


class _CompleteSendSocket:
    def __init__(self):
        self.sent = b""

    def send(self, data):
        self.sent += bytes(data)
        return len(data)

    def sendall(self, data):
        raise AssertionError("non-blocking TCP 不应直接使用 sendall()")

    def close(self):
        pass


class _BackpressureSocket(_CompleteSendSocket):
    def __init__(self):
        super().__init__()
        self.block_once = True

    def send(self, data):
        if self.block_once:
            self.block_once = False
            raise BlockingIOError()
        chunk = bytes(data[:3])
        self.sent += chunk
        return len(chunk)


class TransportTests(unittest.TestCase):
    def test_tcp客户端发现远端关闭后更新连接状态(self):
        local, remote = socket.socketpair()
        transport = TransportWrapper()
        transport.mode = "tcp_client"
        transport._socket = local
        remote.close()
        self.addCleanup(transport.close)

        self.assertEqual(transport.read(16), b"")
        self.assertFalse(transport.is_open)

    def test_tcp客户端完整发送全部数据(self):
        fake_socket = _CompleteSendSocket()
        transport = TransportWrapper()
        transport.mode = "tcp_client"
        transport._socket = fake_socket
        self.addCleanup(transport.close)

        payload = b"a" * 4096
        written = transport.write(payload)

        self.assertEqual(written, len(payload))
        self.assertEqual(fake_socket.sent, payload)

    def test_tcp服务端完整发送全部数据(self):
        fake_socket = _CompleteSendSocket()
        transport = TransportWrapper()
        transport.mode = "tcp_server"
        transport._client_conn = fake_socket
        self.addCleanup(transport.close)

        payload = b"server-response"
        written = transport.write(payload)

        self.assertEqual(written, len(payload))
        self.assertEqual(fake_socket.sent, payload)

    def test_tcp客户端背压后继续发送剩余数据(self):
        fake_socket = _BackpressureSocket()
        transport = TransportWrapper()
        transport.mode = "tcp_client"
        transport._socket = fake_socket
        self.addCleanup(transport.close)

        with mock.patch("select.select", return_value=([], [fake_socket], [])):
            written = transport.write(b"abcdefgh")

        self.assertEqual(written, 8)
        self.assertEqual(fake_socket.sent, b"abcdefgh")
        self.assertTrue(transport.is_open)


if __name__ == "__main__":
    unittest.main()
