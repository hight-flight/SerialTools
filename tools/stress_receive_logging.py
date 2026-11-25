"""以 100ms 周期验证完整接收界面、日志保存及阻塞时的时间戳。

运行：.venv/Scripts/python.exe tools/stress_receive_logging.py
使用软件串口回环和本机 UDP，不连接真实设备；产物保留在 logs 下。
"""

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import serial
from PyQt5.QtCore import QEvent, QEventLoop, QObject, QTimer, Qt, pyqtSlot
from PyQt5.QtWidgets import QApplication

from app_paths import AppPaths
from serial_GUI import SerialTool
from transport import TransportReadThread


STAMP = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6})\] ")
SEQ = re.compile(rb"SEQ=(\d{6})")


def canonical(text):
    return re.sub(r"\s+", "", text)


def interval_stats(times):
    values = sorted(b - a for a, b in zip(times, times[1:]))
    if not values:
        return {}
    return {
        "min_ms": round(values[0] * 1000, 3),
        "median_ms": round(values[len(values) // 2] * 1000, 3),
        "p99_ms": round(values[min(int(len(values) * .99), len(values) - 1)] * 1000, 3),
        "max_ms": round(values[-1] * 1000, 3),
        "gaps_over_1s": sum(value > 1 for value in values),
    }


def inspect_text(text, payloads):
    clean = STAMP.sub("", text)
    ids = [int(value) for value in re.findall(r"SEQ=(\d{6})", clean)]
    counts = Counter(ids)
    complete = []
    corrupt = []
    for match in re.finditer(r"SEQ=(\d{6}).*?END=(\d{6})", clean, re.S):
        seq = int(match[1])
        if seq < len(payloads) and canonical(match[0]) == canonical(payloads[seq].decode("ascii")):
            complete.append(seq)
        else:
            corrupt.append(seq)
    return {
        "sequence_count": len(ids),
        "complete_frames": len(complete),
        "missing_sequences": sorted(set(range(len(payloads))) - set(ids)),
        "duplicate_sequences": sorted(seq for seq, count in counts.items() if count > 1),
        "sequence_order_correct": ids == list(range(len(payloads))),
        "incomplete_or_corrupt_sequences": sorted(set(range(len(payloads))) - set(complete)),
        "corrupt_matches": corrupt,
        "truncation_markers": text.count("...(截断)"),
        "all_payloads_intact": complete == list(range(len(payloads))),
    }


class InstrumentedWindow(SerialTool):
    def __init__(self):
        self.ui_records = []
        super().__init__()

    @pyqtSlot(bytes)
    def handle_receive_data(self, data):
        self.ui_records.append((time.perf_counter(), bytes(data)))
        super().handle_receive_data(data)


class RawRecorder(QObject):
    def __init__(self):
        super().__init__()
        self.records = []

    @pyqtSlot(bytes)
    def receive(self, data):
        # 显式直连，仅在读取线程记录内存数据，不触碰界面或磁盘。
        self.records.append((time.perf_counter(), bytes(data)))


def run_case(app, root, name, count, interval, block_mode=None, large=False):
    case_dir = root / name
    paths = AppPaths(*(case_dir / part for part in ("config", "data", "cache", "logs", "ota")))
    case_dir.mkdir(parents=True)
    payloads = [
        (f"SEQ={seq:06d} weight: {400 + seq % 100}g. PAD="
         + ("X" * 6000 if large else "OK") + f" END={seq:06d}\n").encode("ascii")
        for seq in range(count)
    ]
    expected = b"".join(payloads)
    (case_dir / "sent.bin").write_bytes(expected)
    with ExitStack() as stack:
        stack.enter_context(mock.patch("serial_GUI.resolve_app_paths", return_value=paths))
        stack.enter_context(mock.patch("serial_GUI.migrate_legacy_user_data"))
        stack.enter_context(mock.patch.object(SerialTool, "refresh_ports"))
        stack.enter_context(mock.patch.object(SerialTool, "load_config"))
        window = InstrumentedWindow()
        window.show()
        app.processEvents()
        window.combo_encoding.setCurrentText("UTF-8")
        window.check_hex_recv.setChecked(False)
        window.act_timestamp.setChecked(True)
        window.text_recv.clear()
        window.log_buffer.clear()
        if large:
            # UDP 保证一次读取完整的长报文，单独覆盖日志长度边界。
            window.transport.open_udp("127.0.0.1", 0, "127.0.0.1", 9)
            destination = window.transport._socket.getsockname()
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            stack.callback(sender.close)
            send = lambda payload: sender.sendto(payload, destination)
        else:
            window.transport._serial = serial.serial_for_url("loop://", timeout=1)
            send = window.transport._serial.write

        window.toggle_auto_save(2)
        log_path = Path(window.log_file_path)
        recorder = RawRecorder()
        reader = TransportReadThread(window.transport, window.serial_mutex)
        window.read_thread = reader
        reader.receive_data_signal.connect(recorder.receive, Qt.DirectConnection)
        reader.receive_data_signal.connect(window.handle_receive_data)
        errors = []
        reader.error_signal.connect(errors.append)
        sent = []
        done = threading.Event()
        stop = threading.Event()

        def produce():
            start = time.perf_counter()
            try:
                for seq, payload in enumerate(payloads):
                    if stop.wait(max(0, start + seq * interval - time.perf_counter())):
                        break
                    sent.append({"seq": seq, "monotonic": time.perf_counter(),
                                 "wall_time": datetime.now().isoformat(timespec="microseconds")})
                    if send(payload) != len(payload):
                        errors.append(f"序号 {seq} 未完整发送")
            except Exception as exc:
                errors.append(repr(exc))
            finally:
                done.set()

        blocks = []
        block_timer = QTimer()
        block_timer.setInterval(7000)

        def block_ui():
            start = time.perf_counter()
            time.sleep(1.5)
            blocks.append({"kind": "ui_sleep", "start": start,
                           "duration_ms": (time.perf_counter() - start) * 1000})

        block_timer.timeout.connect(block_ui)
        if block_mode == "ui":
            block_timer.start()

        real_fsync = os.fsync
        log_fd = window.current_log_file.fileno()
        fsync_records = []
        injecting = True

        def measured_fsync(fd):
            start = time.perf_counter()
            if fd == log_fd and injecting and block_mode == "fsync":
                time.sleep(1.2)
            result = real_fsync(fd)
            if fd == log_fd:
                fsync_records.append((time.perf_counter() - start) * 1000)
            return result

        stack.enter_context(mock.patch("serial_GUI.os.fsync", side_effect=measured_fsync))
        loop = QEventLoop()
        drain_timer = QTimer()
        drain_timer.setInterval(25)
        drain_timer.timeout.connect(lambda: loop.quit() if done.is_set() and window.rx_bytes >= len(expected) else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(int((count * interval + 15) * 1000))
        drain_timer.start()
        producer = threading.Thread(target=produce, name="stress-producer", daemon=True)
        started = time.perf_counter()
        reader.start()
        producer.start()
        try:
            loop.exec_()
        finally:
            injecting = False
            stop.set()
            producer.join(timeout=5)
            block_timer.stop()
            drain_timer.stop()
            deadline.stop()
            reader.stop()
            app.processEvents()
            window._flush_pending_display()
            window.toggle_auto_save(0)

        elapsed = time.perf_counter() - started
        raw = b"".join(data for _, data in recorder.records)
        display = window.text_recv.toPlainText()
        saved = log_path.read_text(encoding="utf-8")
        (case_dir / "received.bin").write_bytes(raw)
        (case_dir / "display.txt").write_text(display, encoding="utf-8")
        (case_dir / "sent_timing.json").write_text(json.dumps(sent, indent=2), encoding="utf-8")
        raw_times = {int(m[1]): timestamp for timestamp, data in recorder.records for m in SEQ.finditer(data)}
        ui_times = {int(m[1]): timestamp for timestamp, data in window.ui_records for m in SEQ.finditer(data)}
        log_times = {}
        current_stamp = None
        for line in saved.splitlines():
            match = STAMP.match(line)
            if match:
                current_stamp = datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S.%f").timestamp()
            for seq in re.findall(r"SEQ=(\d{6})", line):
                if current_stamp is not None:
                    log_times[int(seq)] = current_stamp
        delays = [ui_times[seq] - raw_times[seq] for seq in raw_times if seq in ui_times]
        timing = [{**item, "read_monotonic": raw_times.get(item["seq"]),
                   "ui_monotonic": ui_times.get(item["seq"]),
                   "log_wall_timestamp": log_times.get(item["seq"])} for item in sent]
        (case_dir / "pipeline_timing.json").write_text(json.dumps(timing, indent=2), encoding="utf-8")
        result = {
            "case": name, "transport": "localhost_udp" if large else "pyserial_loop",
            "requested_interval_ms": interval * 1000, "sent_frames": len(sent),
            "elapsed_s": round(elapsed, 3), "sent_bytes": len(expected),
            "read_bytes": len(raw), "ui_rx_bytes": window.rx_bytes,
            "raw_stream_exact": raw == expected,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "expected_sha256": hashlib.sha256(expected).hexdigest(),
            "read_chunks": len(recorder.records),
            "send_intervals": interval_stats([item["monotonic"] for item in sent]),
            "read_intervals": interval_stats(list(raw_times.values())),
            "log_intervals": interval_stats(list(log_times.values())),
            "max_read_to_ui_delay_ms": round(max(delays, default=0) * 1000, 3),
            "fsync_calls": len(fsync_records),
            "max_fsync_ms": round(max(fsync_records, default=0), 3),
            "injected_ui_blocks": blocks,
            "saved_log": inspect_text(saved, payloads),
            "display": inspect_text(display, payloads),
            "log_path": str(log_path), "errors": errors,
            "producer_stopped": not producer.is_alive(),
        }
        (case_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        window.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result


def write_report(root, results):
    names = {"normal": "正常接收", "ui_block": "界面每 7 秒阻塞 1.5 秒",
             "slow_fsync": "每次定时刷盘额外延迟 1.2 秒", "long_packet": "约 6KB 长报文"}
    lines = [
        "# 100ms 周期接收与日志压力测试", "",
        "使用完整 SerialTool 窗口、TransportReadThread、QTextEdit 渲染和真实文件自动保存。",
        "正常与阻塞场景通过 pyserial loop:// 软件串口回环输入；长报文通过本机 UDP 输入，保证读取块超过日志长度上限。",
        "窗口使用 Qt offscreen 平台，测试配置、日志均隔离。未使用真实串口硬件，未修改应用接收或保存实现。", "",
        "每包包含连续 SEQ、重量、填充内容和 END 尾部序号；另存原始发送及读取字节，并校验完整字节流 SHA-256。",
        "界面和日志验证每包的序号、顺序、重复、正文及尾部完整性，不把读取块数量当作设备帧数量。", "",
        "| 场景 | 发送包数 | 原始读取完全一致 | 日志完整包数 | 最大读取间隔 | 最大日志时间戳间隔 | 最大读取到界面延迟 |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| {names[result['case']]} | {result['sent_frames']} | "
            f"{'是' if result['raw_stream_exact'] else '否'} | {result['saved_log']['complete_frames']} | "
            f"{result['read_intervals'].get('max_ms', 0):.3f}ms | "
            f"{result['log_intervals'].get('max_ms', 0):.3f}ms | "
            f"{result['max_read_to_ui_delay_ms']:.3f}ms |"
        )
    lines += [
        "", "## 结果解释", "",
        "- 正常场景用于检查本机在每秒 10 包时的基线；该发送速率不代表最大吞吐能力。",
        "- 对比读取间隔与日志间隔，可以判断界面阻塞是否导致时间戳失真；人工慢刷盘是故障注入，不代表实测磁盘本身很慢。",
        "- 原始字节完全一致，但日志缺少 END 或含“截断”标记，说明内容在应用保存之前已经被截断。",
        "- 测试只覆盖本次持续时间、数据大小和配置，不能证明真实设备、驱动或长期运行绝不丢数据。", "",
        "## 代码位置", "",
        "- serial_GUI.py:3092：在界面槽函数内生成当前时间戳。",
        "- serial_GUI.py:1054、5946：界面 QTimer 每 2 秒调用同步 fsync。",
        "- serial_GUI.py:3051：日志和显示共用 4096 字符截断结果。", "",
        "## 复现与产物", "",
        "运行 `.venv/Scripts/python.exe tools/stress_receive_logging.py` 可重跑所有场景。", "",
        "- summary.json：全部指标及完整性检查结果。",
        "- 各场景 sent.bin / received.bin：发送及原始接收字节。",
        "- 各场景 pipeline_timing.json：发送、读取、界面处理和日志时间戳。",
        "- 各场景 display.txt：接收区最终内容；logs/*.txt：应用实际自动保存文件。", "",
    ]
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="缩短场景，用于验证测试工具")
    args = parser.parse_args()
    root = ROOT / "logs" / ("receive-stress-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    cases = [
        ("normal", 30 if args.quick else 600, None, False),
        ("ui_block", 90 if args.quick else 300, "ui", False),
        ("slow_fsync", 40 if args.quick else 300, "fsync", False),
        ("long_packet", 5 if args.quick else 50, None, True),
    ]
    results = []
    print(f"测试产物目录：{root}", flush=True)
    for name, count, mode, large in cases:
        print(f"开始场景：{name}，{count} 包，100ms 周期", flush=True)
        results.append(run_case(app, root, name, count, .1, mode, large))
        (root / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(root, results)
    print(f"测试完成：{root / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
