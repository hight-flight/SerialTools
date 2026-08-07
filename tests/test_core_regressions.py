import unittest
from unittest import mock

from data_viewer import CaptureTableModel, JsonCaptureThread
from theme import unescape_text


class TextEscapeTests(unittest.TestCase):
    def test保留普通中文并解析控制字符转义(self):
        self.assertEqual(unescape_text(r"中文\r\n\t完成"), "中文\r\n\t完成")

    def test支持十六进制和unicode转义(self):
        self.assertEqual(unescape_text(r"A\x42\u4E2D"), "AB中")

    def test无效转义保持原文(self):
        self.assertEqual(unescape_text(r"路径\q保持"), r"路径\q保持")


class DataViewerRegressionTests(unittest.TestCase):
    def test达到捕获上限后滚动保留最新数据(self):
        model = CaptureTableModel()
        model._items = [{"summary": "old-1"}, {"summary": "old-2"}, {"summary": "old-3"}]

        with mock.patch("data_viewer.MAX_CAPTURE_ITEMS", 3):
            model.append_items([{"summary": "new"}])

        self.assertEqual([item["summary"] for item in model._items], ["old-2", "old-3", "new"])

    def test合法但值为假的json不标记为解析错误(self):
        capture = JsonCaptureThread()

        for raw in ("{}", "[]", "0", "false", "null"):
            with self.subTest(raw=raw):
                _raw, _obj, _summary, parse_error = capture._try_parse(raw)
                self.assertFalse(parse_error)

    def test逐行解析保留跨数据块的utf8字符(self):
        capture = JsonCaptureThread()
        payload = '{"名称":"测试"}\n'.encode("utf-8")
        split_at = payload.index("测".encode("utf-8")) + 2

        capture._byte_buffer.extend(payload[:split_at])
        self.assertEqual(capture._extract_lines(), [])
        capture._byte_buffer.extend(payload[split_at:])

        batch = capture._extract_lines()
        self.assertEqual(batch[0][1], {"名称": "测试"})
        self.assertFalse(batch[0][3])

    def test对象解析保留跨数据块的utf8字符(self):
        capture = JsonCaptureThread()
        payload = '{"序号":1}{"名称":"测试"}'.encode("utf-8")
        split_at = payload.index("测".encode("utf-8")) + 1

        capture._byte_buffer.extend(payload[:split_at])
        first_batch = capture._extract_json_objects()
        self.assertEqual(first_batch[0][1], {"序号": 1})
        capture._byte_buffer.extend(payload[split_at:])

        batch = capture._extract_json_objects()
        self.assertEqual(batch[0][1], {"名称": "测试"})

    def test正则模式不消费尚未换行的半包(self):
        capture = JsonCaptureThread()
        capture.custom_regex = r"TEMP=\d+"
        capture._byte_buffer.extend(b"TEMP=12")

        self.assertEqual(capture._extract_regex(), [])
        self.assertEqual(bytes(capture._byte_buffer), b"TEMP=12")

        capture._byte_buffer.extend(b"\n")
        batch = capture._extract_regex()
        self.assertEqual(batch[0][0], "TEMP=12")


if __name__ == "__main__":
    unittest.main()
