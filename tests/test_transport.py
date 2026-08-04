import socket
import unittest

from transport import TransportWrapper


class _CompleteSendSocket:
    def __init__(self):
        self.sent = b""

    def send(self, _data):
        raise AssertionError("TCP 不应使用可能部分写入的 send()")

    def sendall(self, data):
        self.sent += data

    def close(self):
        pass


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


if __name__ == "__main__":
    unittest.main()
