#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据分析面板 — 独立模块
功能：实时捕获串口/网络 JSON 数据流，列表/树形/表格/图表展示分析

依赖：PyQt5, pyqtgraph, numpy（标准库之外）
"""

import sys
import json
import re
import time
import datetime
from collections import deque
from typing import Optional

from PyQt5.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QCheckBox, QMessageBox,
    QSplitter, QLineEdit, QFrame, QSpinBox, QGroupBox,
    QTableView, QTreeView, QHeaderView, QAbstractItemView,
    QTableWidget, QTableWidgetItem, QPlainTextEdit,
    QStackedWidget, QMenu, QAction, QFileDialog, QInputDialog,
    QColorDialog, QToolTip,
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QMutex, QMutexLocker,
    QAbstractTableModel, QModelIndex, QByteArray, QMimeData, QPoint, QEvent,
)
from PyQt5.QtGui import (
    QFont, QColor, QSyntaxHighlighter, QTextCharFormat,
    QStandardItemModel, QStandardItem, QCursor,
)

try:
    import pyqtgraph as pg
    import numpy as np
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False

# ──────────────────────────────────────────────
#  常量
# ──────────────────────────────────────────────
MAX_CAPTURE_ITEMS = 100_000          # 捕获列表最大条目
MAX_CURVE_POINTS = 5000             # 每条曲线最大原始点数
BATCH_UPDATE_MS = 30                # 批量更新间隔 ms（低延迟）
CHART_REFRESH_MS = 50               # 图表刷新间隔 ms
SEARCH_DEBOUNCE_MS = 200            # 搜索防抖 ms
SUMMARY_MAX_LEN = 60                # 摘要截断长度

# ── 色彩令牌（与 theme.py 主主题对齐，统一管理所有硬编码色值）──
_VIEWER_TOKENS = {
    'dark': {
        'bg_page':        '#282C34',
        'bg_card':        '#2C313C',
        'bg_header':      '#21252B',
        'bg_alt':         '#252830',
        'bg_readonly':    '#252526',
        'bg_menu':        '#21252B',
        'text_primary':   '#ABB2BF',
        'text_secondary': '#8B95A5',
        'text_muted':     '#888888',
        'text_inverse':   '#FFFFFF',
        'border_default': '#3E4451',
        'border_light':   '#2C313C',
        'accent':         '#528BFF',
        'accent_hover':   '#61AFEF',
        'accent_surface': '#3E5A8C',
        'hover_row':      'rgba(82, 139, 255, 60)',
        'hover_header':   '#2C313C',
        'selection_bg':   '#3E5A8C',
        'selection_fg':   '#FFFFFF',
        'danger':         '#E06C75',
        'success':        '#98C379',
        'warning':        '#E5C07B',
        'chart_bg':       '#2C313C',
        'chart_text':     '#ABB2BF',
    },
    'light': {
        'bg_page':        '#F5F5F5',
        'bg_card':        '#FFFFFF',
        'bg_header':      '#E8E8E8',
        'bg_alt':         '#F5F5F5',
        'bg_readonly':    '#F0F0F0',
        'bg_menu':        '#FFFFFF',
        'text_primary':   '#333333',
        'text_secondary': '#333333',
        'text_muted':     '#888888',
        'text_inverse':   '#FFFFFF',
        'border_default': '#CCCCCC',
        'border_light':   '#E0E0E0',
        'accent':         '#0078D4',
        'accent_hover':   '#168BE0',
        'accent_surface': '#0078D4',
        'hover_row':      'rgba(0, 120, 212, 50)',
        'hover_header':   '#D0D0D0',
        'selection_bg':   '#0078D4',
        'selection_fg':   '#FFFFFF',
        'danger':         '#E06C75',
        'success':        '#508C50',
        'warning':        '#C07020',
        'chart_bg':       '#FFFFFF',
        'chart_text':     '#666666',
    },
}

CURVE_COLORS = [
    (0x61, 0xAF, 0xEF), (0x98, 0xC3, 0x79), (0xE0, 0x6C, 0x75),
    (0xD1, 0x9A, 0x66), (0xC6, 0x78, 0xDD), (0x56, 0xB6, 0xC2),
    (0xAB, 0xB2, 0xBF), (0xE5, 0xC0, 0x7B), (0xBE, 0x50, 0x46),
    (0x46, 0xBE, 0x8C), (0xBE, 0x46, 0xBE), (0x8C, 0xBE, 0x46),
]

# JSON 类型着色配置: (r, g, b) 元组，在 dark/light 下复用
JSON_COLORS = {
    'key':       (0x61, 0xAF, 0xEF),   # 蓝 — 键名
    'string':    (0x98, 0xC3, 0x79),   # 绿 — 字符串
    'number':    (0xD1, 0x9A, 0x66),   # 橙 — 数字
    'boolean':   (0x56, 0xB6, 0xC2),   # 青 — 布尔
    'null':      (0xAB, 0xB2, 0xBF),   # 灰 — null
    'array':     (0xE0, 0x6C, 0x75),   # 红 — 数组
    'object':    (0xC6, 0x78, 0xDD),   # 紫 — 对象
    'bracket':   (0xAB, 0xB2, 0xBF),   # 灰 — 括号
    'key_dark':  (0x82, 0xBF, 0xFF),   # 暗色主题下键名更亮
}


# ──────────────────────────────────────────────
#  二进制协议解析：类型映射 & 数据类
# ──────────────────────────────────────────────
import struct as _struct

_DTYPE_MAP = {
    # 无符号整数
    'uint8':      ('B', 1, ''),
    'uint16_le':  ('H', 2, '<'),
    'uint16_be':  ('H', 2, '>'),
    'uint32_le':  ('I', 4, '<'),
    'uint32_be':  ('I', 4, '>'),
    'uint64_le':  ('Q', 8, '<'),
    'uint64_be':  ('Q', 8, '>'),
    # 有符号整数
    'int8':       ('b', 1, ''),
    'int16_le':   ('h', 2, '<'),
    'int16_be':   ('h', 2, '>'),
    'int32_le':   ('i', 4, '<'),
    'int32_be':   ('i', 4, '>'),
    'int64_le':   ('q', 8, '<'),
    'int64_be':   ('q', 8, '>'),
    # 浮点
    'float32_le': ('f', 4, '<'),
    'float32_be': ('f', 4, '>'),
    'float64_le': ('d', 8, '<'),
    'float64_be': ('d', 8, '>'),
}

_DTYPE_CHOICES = list(_DTYPE_MAP.keys())


def unpack_frame(frame: bytes, fields: list) -> dict | None:
    """将一帧二进制数据按字段列表解包为 dict。fields 是 ProtoField 列表。

    返回的 dict 包含每个字段的解析值 + `_raw_hex`（字段级HEX）+ `_frame_hex`（全帧HEX）。
    """
    if not fields:
        return None
    result = {}
    raw_hex_parts = []
    for f in fields:
        try:
            info = _DTYPE_MAP.get(f.dtype)
            if info is None:
                result[f.name] = None
                raw_hex_parts.append('--')
                continue
            fmt_char, size, endian = info
            if f.offset + size > len(frame):
                result[f.name] = None
                raw_hex_parts.append(frame[f.offset:].hex(' ').upper() if f.offset < len(frame) else '--')
                continue

            chunk = frame[f.offset : f.offset + size]
            raw_val = _struct.unpack(endian + fmt_char, chunk)[0]

            # 位域提取
            if f.bit_width > 0:
                mask = (1 << f.bit_width) - 1
                val = (raw_val >> f.bit_offset) & mask
            else:
                val = raw_val

            # 缩放
            if f.scale != 1.0:
                val = round(val * f.scale, 6)

            result[f.name] = val
            raw_hex_parts.append(chunk.hex(' ').upper())
        except Exception:
            result[f.name] = None
            raw_hex_parts.append('ERR')

    result['_raw_hex'] = ' '.join(raw_hex_parts) if raw_hex_parts else ''
    result['_frame_hex'] = frame.hex(' ').upper()
    return result


class ProtoField:
    """二进制协议单个字段定义"""
    __slots__ = ('name', 'dtype', 'offset', 'scale', 'unit', 'bit_offset', 'bit_width')

    def __init__(self, name="", dtype="uint8", offset=0, scale=1.0, unit="",
                 bit_offset=0, bit_width=0):
        self.name = name
        self.dtype = dtype
        self.offset = offset
        self.scale = scale
        self.unit = unit
        self.bit_offset = bit_offset
        self.bit_width = bit_width

    def to_dict(self):
        return {
            'name': self.name, 'dtype': self.dtype, 'offset': self.offset,
            'scale': self.scale, 'unit': self.unit,
            'bit_offset': self.bit_offset, 'bit_width': self.bit_width,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            name=d.get('name', ''), dtype=d.get('dtype', 'uint8'),
            offset=d.get('offset', 0), scale=d.get('scale', 1.0),
            unit=d.get('unit', ''), bit_offset=d.get('bit_offset', 0),
            bit_width=d.get('bit_width', 0),
        )


class FrameSync:
    """二进制帧同步配置"""
    __slots__ = ('mode', 'header', 'frame_len', 'len_offset', 'len_dtype', 'len_adjust')

    def __init__(self, mode="delimiter", header="", frame_len=0,
                 len_offset=0, len_dtype="uint8", len_adjust=0):
        self.mode = mode            # "delimiter" | "fixed_length" | "length_field"
        self.header = header        # HEX string, e.g. "AA55"
        self.frame_len = frame_len
        self.len_offset = len_offset
        self.len_dtype = len_dtype
        self.len_adjust = len_adjust

    def to_dict(self):
        return {
            'mode': self.mode, 'header': self.header, 'frame_len': self.frame_len,
            'len_offset': self.len_offset, 'len_dtype': self.len_dtype,
            'len_adjust': self.len_adjust,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            mode=d.get('mode', 'delimiter'), header=d.get('header', ''),
            frame_len=d.get('frame_len', 0), len_offset=d.get('len_offset', 0),
            len_dtype=d.get('len_dtype', 'uint8'), len_adjust=d.get('len_adjust', 0),
        )


class ProtocolTemplate:
    """完整二进制协议模板"""
    def __init__(self, name="", description="", frame_sync=None, fields=None):
        self.name = name
        self.description = description
        self.frame_sync = frame_sync or FrameSync()
        self.fields: list[ProtoField] = fields or []

    @property
    def frame_len_calc(self) -> int:
        """根据字段定义计算的帧长度（字节）"""
        total = 0
        for f in self.fields:
            info = _DTYPE_MAP.get(f.dtype)
            if info:
                total = max(total, f.offset + info[1])
        return total

    def to_dict(self):
        return {
            'name': self.name, 'description': self.description,
            'frame_sync': self.frame_sync.to_dict(),
            'fields': [f.to_dict() for f in self.fields],
        }

    @classmethod
    def from_dict(cls, d):
        fs = FrameSync.from_dict(d.get('frame_sync', {}))
        fields = [ProtoField.from_dict(fd) for fd in d.get('fields', [])]
        return cls(
            name=d.get('name', ''), description=d.get('description', ''),
            frame_sync=fs, fields=fields,
        )


# ──────────────────────────────────────────────
#  LTTB 降采样算法
# ──────────────────────────────────────────────
def lttb_downsample(data, target_width):
    """Largest Triangle Three Buckets 降采样，保持视觉趋势"""
    n = len(data)
    if n <= target_width or target_width < 3:
        return data

    result = []
    bucket_size = (n - 2) / (target_width - 2)
    result.append(data[0])

    for i in range(1, target_width - 1):
        start = int((i - 1) * bucket_size) + 1
        end = int(i * bucket_size) + 1
        if end <= start:
            end = start + 1
        if end > n - 1:
            end = n - 1

        prev_pt = result[-1]
        next_pt = data[end]

        max_area = -1
        max_idx = start
        for j in range(start, end):
            area = abs(
                (prev_pt[0] - next_pt[0]) * (data[j][1] - prev_pt[1]) -
                (prev_pt[0] - data[j][0]) * (next_pt[1] - prev_pt[1])
            )
            if area > max_area:
                max_area = area
                max_idx = j

        result.append(data[max_idx])

    result.append(data[-1])
    return result


# ──────────────────────────────────────────────
#  JSON 语法高亮器
# ──────────────────────────────────────────────
class JsonSyntaxHighlighter(QSyntaxHighlighter):
    """对原始 JSON 文本进行正则语法着色"""

    def __init__(self, parent=None, is_dark=True):
        super().__init__(parent)
        self._init_formats(is_dark)

    def _init_formats(self, is_dark=True):
        def _fmt(r, g, b, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(r, g, b))
            if bold: f.setFontWeight(700)
            if italic: f.setFontItalic(True)
            return f

        # 暗色 / 明亮下使用不同配色
        if is_dark:
            str_color = (0x98, 0xC3, 0x79)
            num_color = (0xD1, 0x9A, 0x66)
            bool_color = (0x56, 0xB6, 0xC2)
            null_color = (0xAB, 0xB2, 0xBF)
            key_color = (0x82, 0xBF, 0xFF)
            gray_fg = (0xAB, 0xB2, 0xBF)
        else:
            str_color = (0x50, 0x8C, 0x50)   # 深绿
            num_color = (0xC0, 0x70, 0x20)   # 深橙
            bool_color = (0x20, 0x80, 0x90)  # 深青
            null_color = (0x88, 0x88, 0x88)  # 灰
            key_color = (0x10, 0x60, 0xC0)   # 深蓝
            gray_fg = (0x88, 0x88, 0x88)

        self.rules = [
            (r'"(?:\\.|[^"\\])*"', _fmt(*str_color)),
            (r'\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b', _fmt(*num_color)),
            (r'\b(?:true|false)\b', _fmt(*bool_color, bold=True)),
            (r'\bnull\b', _fmt(*null_color, italic=True)),
            (r'"(?:\\.|[^"\\])*"\s*:', _fmt(*key_color)),
            (r'[\[\]\{\}]', _fmt(*gray_fg, bold=True)),
            (r'[:,]', _fmt(*gray_fg)),
        ]

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            for m in re.finditer(pattern, text):
                start, end = m.start(), m.end()
                self.setFormat(start, end - start, fmt)


# ──────────────────────────────────────────────
#  捕获列表数据模型
# ──────────────────────────────────────────────
class CaptureTableModel(QAbstractTableModel):
    """高性能 JSON 捕获列表模型，支持 10 万+ 条数据"""

    HEADERS = ["序号", "时间", "摘要", "长度"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []          # 全量数据
        self._filtered: list[int] = []        # 筛选后的索引列表
        self._filter_text = ""                # 当前搜索文本
        self._filter_enabled = False

    # --- 数据管理 ---
    def append_items(self, new_items: list[dict]):
        """批量追加条目"""
        if not new_items:
            return
        start = len(self._items)
        total = start + len(new_items)

        # 超限则丢弃最旧数据，需要完全重置模型
        if total > MAX_CAPTURE_ITEMS:
            discard = total - MAX_CAPTURE_ITEMS
            if discard >= len(new_items):
                return  # 全部被丢弃
            del self._items[:discard]
            start = len(self._items)
            # 有删除又有新增，直接全量刷新避免索引混乱
            self._items.extend(new_items)
            if self._filter_enabled:
                self._rebuild_filter()  # 内部有 beginResetModel/endResetModel
            else:
                self.beginResetModel()
                self.endResetModel()
            return

        self.beginInsertRows(QModelIndex(), start, start + len(new_items) - 1)
        self._items.extend(new_items)
        self.endInsertRows()

        # 有筛选时增量更新筛选索引
        if self._filter_enabled and self._filter_text:
            txt = self._filter_text
            for i in range(start, start + len(new_items)):
                summary = self._items[i].get('summary', '').lower()
                if txt in summary:
                    self._filtered.append(i)

    def clear(self):
        self.beginResetModel()
        self._items.clear()
        self._filtered.clear()
        self.endResetModel()

    def get_item(self, row: int) -> Optional[dict]:
        """获取某行对应的 JSON 对象"""
        if self._filter_enabled:
            if 0 <= row < len(self._filtered):
                idx = self._filtered[row]
                if 0 <= idx < len(self._items):
                    return self._items[idx]
        else:
            if 0 <= row < len(self._items):
                return self._items[row]
        return None

    def remove_rows(self, indices: list[int]):
        """删除指定行（indices 为当前显示视图中的行号）"""
        if self._filter_enabled:
            real_indices = sorted(
                [self._filtered[i] for i in indices if 0 <= i < len(self._filtered)], reverse=True
            )
        else:
            real_indices = sorted(indices, reverse=True)

        for idx in real_indices:
            if 0 <= idx < len(self._items):
                del self._items[idx]

        if self._filter_enabled:
            self._rebuild_filter()  # 内部有 beginResetModel/endResetModel
        else:
            self.beginResetModel()
            self.endResetModel()

    # --- 筛选 ---
    def set_filter(self, text: str):
        self._filter_text = text.lower() if text else ""
        self._filter_enabled = bool(self._filter_text)
        self._rebuild_filter()

    def _rebuild_filter(self):
        self.beginResetModel()
        self._filtered.clear()
        if not self._filter_enabled:
            self.endResetModel()
            return
        txt = self._filter_text
        for i, item in enumerate(self._items):
            summary = item.get('summary', '').lower()
            if txt in summary:
                self._filtered.append(i)
        self.endResetModel()

    @property
    def total_count(self) -> int:
        return len(self._items)

    @property
    def visible_count(self) -> int:
        return len(self._filtered) if self._filter_enabled else len(self._items)

    # --- QAbstractTableModel 接口 ---
    def rowCount(self, parent=QModelIndex()):
        return len(self._filtered) if self._filter_enabled else len(self._items)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        if self._filter_enabled:
            if index.row() >= len(self._filtered):
                return None
            item = self._items[self._filtered[index.row()]]
        else:
            if index.row() >= len(self._items):
                return None
            item = self._items[index.row()]

        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return item.get('seq', index.row() + 1)
            elif col == 1:
                return item.get('timestamp', '')
            elif col == 2:
                return item.get('summary', '')
            elif col == 3:
                return item.get('length', 0)

        elif role == Qt.ForegroundRole:
            if item.get('parse_error'):
                return QColor(0xE0, 0x6C, 0x75)  # 解析失败：红色文字（更醒目）
            return None

        elif role == Qt.BackgroundRole:
            if item.get('parse_error'):
                return QColor(0x3A, 0x28, 0x28)  # 暗红背景标记错误行
            return None

        elif role == Qt.UserRole:
            # 返回原始 JSON 字符串供详情查看
            return item.get('raw', '')

        elif role == Qt.UserRole + 1:
            # 返回解析后的 dict 或 None
            return item.get('obj', None)

        return None


# ──────────────────────────────────────────────
#  JSON 树形模型
# ──────────────────────────────────────────────
def _build_json_tree(obj, parent_item=None, path=""):
    """递归构建 JSON 树节点"""
    if parent_item is None:
        parent_item = QStandardItem("root")
        parent_item.setData("$", Qt.UserRole)  # 路径标记
        parent_item.setEditable(False)

    if isinstance(obj, dict):
        for k, v in obj.items():
            child_path = f"{path}.{k}" if path else f"$.{k}"
            key_item = QStandardItem(str(k))
            key_item.setData(child_path, Qt.UserRole)
            key_item.setEditable(False)
            key_item.setForeground(QColor(*JSON_COLORS['key']))

            val_item = _json_value_to_item(v, child_path)
            parent_item.appendRow([key_item, val_item])

    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            child_path = f"{path}[{i}]"
            idx_item = QStandardItem(f"[{i}]")
            idx_item.setData(child_path, Qt.UserRole)
            idx_item.setEditable(False)
            idx_item.setForeground(QColor(*JSON_COLORS['array']))

            val_item = _json_value_to_item(v, child_path)
            parent_item.appendRow([idx_item, val_item])

    return parent_item


def _json_value_to_item(value, path=""):
    """根据 JSON 值类型创建 QStandardItem"""
    if isinstance(value, dict):
        item = QStandardItem(f"{{{len(value)} keys}}")
        item.setForeground(QColor(*JSON_COLORS['object']))
        item.setEditable(False)
        item.setData(path, Qt.UserRole)
        _build_json_tree(value, item, path)
    elif isinstance(value, list):
        item = QStandardItem(f"[{len(value)} items]")
        item.setForeground(QColor(*JSON_COLORS['array']))
        item.setEditable(False)
        item.setData(path, Qt.UserRole)
        _build_json_tree(value, item, path)
    elif isinstance(value, bool):
        item = QStandardItem("true" if value else "false")
        item.setForeground(QColor(*JSON_COLORS['boolean']))
        item.setEditable(False)
        item.setData(path, Qt.UserRole)
    elif value is None:
        item = QStandardItem("null")
        item.setForeground(QColor(*JSON_COLORS['null']))
        item.setEditable(False)
        item.setData(path, Qt.UserRole)
    elif isinstance(value, (int, float)):
        item = QStandardItem(str(value))
        item.setForeground(QColor(*JSON_COLORS['number']))
        item.setEditable(False)
        item.setData(path, Qt.UserRole)
    else:
        item = QStandardItem(str(value))
        item.setEditable(False)
        item.setData(path, Qt.UserRole)

    return item


# ──────────────────────────────────────────────
#  跟踪字段标签 Chip
# ──────────────────────────────────────────────
class TrackChip(QWidget):
    """可关闭的彩色跟踪字段标签"""
    removed = pyqtSignal(object)       # 携带自身引用
    threshold_changed = pyqtSignal()

    def __init__(self, path: str, color: tuple, alias: str = "", parent=None):
        super().__init__(parent)
        self.path = path
        self.color = color
        self.alias = alias or path
        self.threshold_upper = None
        self.threshold_lower = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # 色标
        dot = QLabel("●")
        r, g, b = color
        dot.setStyleSheet(f"color: rgb({r},{g},{b}); font-size: 12px;")
        layout.addWidget(dot)

        # 标签文字
        self.label = QLabel(self.alias)
        self.label.setFont(QFont("Consolas", 9))
        layout.addWidget(self.label)

        # 关闭按钮
        btn_close = QPushButton("×")
        btn_close.setFixedSize(18, 18)
        btn_close.setFont(QFont("Arial", 9))
        btn_close.setStyleSheet("QPushButton { border: none; padding: 0; }")
        btn_close.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(btn_close)

    def set_alias(self, alias: str):
        self.alias = alias
        self.label.setText(alias)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_alias = menu.addAction("设置别名...")
        act_upper = menu.addAction("设置上限阈值...")
        act_lower = menu.addAction("设置下限阈值...")
        menu.addSeparator()
        act_clear_thresh = menu.addAction("清除阈值")
        act_color = menu.addAction("更改颜色...")

        action = menu.exec_(event.globalPos())
        if action == act_alias:
            text, ok = QInputDialog.getText(self, "别名", "输入别名:", text=self.alias)
            if ok and text:
                self.set_alias(text)
        elif action == act_upper:
            val, ok = QInputDialog.getDouble(self, "上限阈值", "输入上限值:", decimals=4)
            if ok:
                self.threshold_upper = val
                self.threshold_changed.emit()
        elif action == act_lower:
            val, ok = QInputDialog.getDouble(self, "下限阈值", "输入下限值:", decimals=4)
            if ok:
                self.threshold_lower = val
                self.threshold_changed.emit()
        elif action == act_clear_thresh:
            self.threshold_upper = None
            self.threshold_lower = None
            self.threshold_changed.emit()
        elif action == act_color:
            c = QColorDialog.getColor(QColor(*self.color), self)
            if c.isValid():
                self.color = (c.red(), c.green(), c.blue())
                dot.setStyleSheet(f"color: rgb({self.color[0]},{self.color[1]},{self.color[2]}); font-size: 12px;")


# ──────────────────────────────────────────────
#  图表跟踪器面板
# ──────────────────────────────────────────────
class ChartTrackerWidget(QWidget):
    """实时折线图跟踪器，支持多字段、多Y轴、阈值线、告警、散点图、计算字段"""

    alert_state_changed = pyqtSignal(bool)  # Feature 2: 告警状态变化信号
    toggle_data_table_requested = pyqtSignal()  # Feature 4: 请求切换数据表格显隐

    def __init__(self, parent=None, is_dark=True):
        super().__init__(parent)
        if not HAS_PYQTGRAPH:
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("⚠ 需要安装 pyqtgraph 和 numpy 以启用图表功能"))
            return

        self._is_dark = is_dark
        self._paused = False
        self._tracked: dict[str, dict] = {}  # path -> {chip, curve, buffer, viewbox, ...}
        self._color_idx = 0

        # Feature 2: 告警状态
        self._alerts: dict[str, dict] = {}    # path -> {active, count, last_trigger}
        self._alert_active_global = False

        # Feature 5: 散点图模式
        self._chart_mode = 'line'             # 'line' | 'scatter'
        self._scatter_x_path = None
        self._scatter_y_path = None
        self._scatter_manual = False          # 用户是否手动选择过 X/Y

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── 字段标签栏 ──
        chips_wrapper = QWidget()
        self.chips_layout = QHBoxLayout(chips_wrapper)
        self.chips_layout.setContentsMargins(4, 2, 4, 2)
        self.chips_layout.setSpacing(4)
        self.chips_layout.setAlignment(Qt.AlignLeft)

        self.edit_add_field = QLineEdit()
        self.edit_add_field.setPlaceholderText("+添加字段 (输入路径)")
        self.edit_add_field.setFont(QFont("Consolas", 9))
        self.edit_add_field.setMaximumWidth(180)
        self.edit_add_field.returnPressed.connect(self._on_add_field)
        self.chips_layout.addWidget(self.edit_add_field)
        self.chips_layout.addStretch()

        layout.addWidget(chips_wrapper)

        # ── 绘图区 ──
        t = _VIEWER_TOKENS['dark' if is_dark else 'light']
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground(t['chart_bg'])
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('bottom', '采样点')
        self.plot_widget.addLegend()
        self.plot_widget.getPlotItem().setDownsampling(auto=True, mode='peak')
        # 启用鼠标交互
        self.plot_widget.setMouseTracking(True)

        text_color_rgb = (0xAB, 0xB2, 0xBF) if is_dark else (0x66, 0x66, 0x66)
        self.empty_text = pg.TextItem('在上方树形视图拖拽字段或在下框输入路径，回车开始跟踪', color=text_color_rgb, anchor=(0.5, 0.5))
        self.empty_text.setFont(QFont("Microsoft YaHei", 10))
        self.plot_widget.addItem(self.empty_text)

        # 图表右上角统计摘要（挂在 viewport 上确保不被 pyqtgraph 绘制覆盖）
        self.stats_overlay = QLabel(self.plot_widget.viewport())
        self.stats_overlay.setFont(QFont("Consolas", 8))
        self.stats_overlay.setStyleSheet(
            f"QLabel {{"
            f"  background-color: rgba(0,0,0,0.55);"
            f"  color: {t['text_primary']};"
            f"  border-radius: 4px;"
            f"  padding: 4px 8px;"
            f"}}"
        )
        self.stats_overlay.setMinimumWidth(220)
        self.stats_overlay.hide()
        # 跟随父控件大小变化重新定位
        self.plot_widget.installEventFilter(self)

        # Feature 5: 散点图 — 多字段采样序号模式（每字段独立颜色）
        self._scatter_items: dict[str, pg.ScatterPlotItem] = {}
        # Feature 5: 散点图 — 字段-vs-字段 相关性模式 + 回归线
        self.scatter_curve = pg.ScatterPlotItem(
            size=8, pen=pg.mkPen(None),
            brush=pg.mkBrush(0x61, 0xAF, 0xEF, 180)
        )
        self.plot_widget.addItem(self.scatter_curve)
        self.scatter_curve.hide()

        self.regression_line = self.plot_widget.plot(
            [], [], pen=pg.mkPen(color=(0xE0, 0x6C, 0x75), width=1.5, style=Qt.DashLine)
        )
        self.regression_line.hide()

        # 十字准线和值提示
        self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color=(0xFF, 0xFF, 0xFF, 80)))
        self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color=(0xFF, 0xFF, 0xFF, 80)))
        self.plot_widget.addItem(self.crosshair_v, ignoreBounds=True)
        self.plot_widget.addItem(self.crosshair_h, ignoreBounds=True)
        self.crosshair_v.hide()
        self.crosshair_h.hide()

        # 悬停代理
        self.hover_proxy = pg.SignalProxy(
            self.plot_widget.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved
        )

        # 优化 pyqtgraph 左下角"A"自动范围按钮：放大尺寸、提高不透明度、加 tooltip
        _auto_btn = self.plot_widget.getPlotItem().autoBtn
        if _auto_btn is not None:
            _auto_btn.setScale(1.6)       # 14px → ~22px，更容易点击
            _auto_btn.setOpacity(1.0)     # 0.7 → 1.0，更醒目
            _auto_btn.setToolTip("点击恢复自动跟踪（缩放后出现）")

        layout.addWidget(self.plot_widget, stretch=1)

        # 用事件过滤器让 PlotWidget 的拖放事件传递到 ChartTrackerWidget
        self.plot_widget.installEventFilter(self)

        # ── 控制按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setFont(QFont("Microsoft YaHei", 9))
        self.btn_pause.setCheckable(True)
        self.btn_pause.setToolTip("暂停/继续图表更新 (Space)")
        self.btn_pause.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self.btn_pause)

        self.btn_clear_chart = QPushButton("清空")
        self.btn_clear_chart.setFont(QFont("Microsoft YaHei", 9))
        self.btn_clear_chart.setToolTip("清空图表曲线数据（保留跟踪字段配置）")
        self.btn_clear_chart.clicked.connect(self.clear_all_data)
        btn_row.addWidget(self.btn_clear_chart)

        btn_row.addSpacing(8)

        # Feature 5: 散点图模式切换按钮（始终可见）
        self.btn_scatter_mode = QPushButton("切换散点图")
        self.btn_scatter_mode.setFont(QFont("Microsoft YaHei", 9))
        self.btn_scatter_mode.setCheckable(True)
        self.btn_scatter_mode.setToolTip("在折线图和散点图之间切换")
        self.btn_scatter_mode.clicked.connect(self._toggle_chart_mode)
        btn_row.addWidget(self.btn_scatter_mode)

        # Feature 5: 散点图 X/Y 控件组（仅散点图模式可见）
        self.scatter_frame = QFrame()
        self.scatter_frame.setFrameShape(QFrame.StyledPanel)
        self.scatter_frame.setStyleSheet(
            f"QFrame {{ border: 1px solid {t['border_default']}; border-radius: 4px; padding: 2px 4px; }}"
        )
        self.scatter_frame.hide()
        scatter_layout = QHBoxLayout(self.scatter_frame)
        scatter_layout.setContentsMargins(2, 2, 2, 2)
        scatter_layout.setSpacing(3)

        self.lbl_x_field = QLabel("X:")
        self.lbl_x_field.setFont(QFont("Microsoft YaHei", 9))
        scatter_layout.addWidget(self.lbl_x_field)

        self.combo_x_field = QComboBox()
        self.combo_x_field.setFont(QFont("Microsoft YaHei", 9))
        self.combo_x_field.setToolTip("散点图 X 轴字段")
        self.combo_x_field.setMinimumWidth(100)
        self.combo_x_field.currentIndexChanged.connect(self._on_scatter_field_changed)
        scatter_layout.addWidget(self.combo_x_field)

        self.lbl_y_field = QLabel("Y:")
        self.lbl_y_field.setFont(QFont("Microsoft YaHei", 9))
        scatter_layout.addWidget(self.lbl_y_field)

        self.combo_y_field = QComboBox()
        self.combo_y_field.setFont(QFont("Microsoft YaHei", 9))
        self.combo_y_field.setToolTip("散点图 Y 轴字段")
        self.combo_y_field.setMinimumWidth(100)
        self.combo_y_field.currentIndexChanged.connect(self._on_scatter_field_changed)
        scatter_layout.addWidget(self.combo_y_field)

        btn_row.addWidget(self.scatter_frame)

        # Feature 3: 计算字段按钮
        self.btn_add_computed = QPushButton("fx 计算字段")
        self.btn_add_computed.setFont(QFont("Microsoft YaHei", 9))
        self.btn_add_computed.setToolTip("添加计算字段 (如: volt * 100)")
        self.btn_add_computed.clicked.connect(self._add_computed_field)
        btn_row.addWidget(self.btn_add_computed)

        btn_row.addSpacing(4)

        # Feature 4: 数据表格切换（在图表区，紧邻数据操作）
        self.btn_toggle_table = QPushButton("⊞ 数据表")
        self.btn_toggle_table.setFont(QFont("Microsoft YaHei", 9))
        self.btn_toggle_table.setToolTip("显示/隐藏图表下方的实时数据表格")
        self.btn_toggle_table.clicked.connect(self.toggle_data_table_requested.emit)
        btn_row.addWidget(self.btn_toggle_table)

        btn_png = QPushButton("导出 PNG")
        btn_png.setFont(QFont("Microsoft YaHei", 9))
        btn_png.clicked.connect(self._export_png)
        btn_row.addWidget(btn_png)

        btn_csv = QPushButton("导出 CSV")
        btn_csv.setFont(QFont("Microsoft YaHei", 9))
        btn_csv.clicked.connect(self._export_csv)
        btn_row.addWidget(btn_csv)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── 刷新定时器（有跟踪字段时才启动）──
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._update_plot)
        self.refresh_timer.setInterval(CHART_REFRESH_MS)

        # 数据点全局 X 索引
        self._point_index = 0

        # 接受拖放
        self.setAcceptDrops(True)

    def set_theme(self, is_dark: bool):
        """响应主题切换，更新绘图区背景和告警边框"""
        if not HAS_PYQTGRAPH:
            return
        self._is_dark = is_dark
        t = _VIEWER_TOKENS['dark' if is_dark else 'light']
        self.plot_widget.setBackground(t['chart_bg'])
        text_color_rgb = (0xAB, 0xB2, 0xBF) if is_dark else (0x66, 0x66, 0x66)
        self.empty_text.setColor(text_color_rgb)
        # 统计浮层主题
        if is_dark:
            self.stats_overlay.setStyleSheet(
                f"QLabel {{ background-color: rgba(0,0,0,0.55); color: {t['text_primary']};"
                f" border-radius: 4px; padding: 4px 8px; }}"
            )
        else:
            self.stats_overlay.setStyleSheet(
                f"QLabel {{ background-color: rgba(240,240,240,0.85); color: {t['text_primary']};"
                f" border-radius: 4px; padding: 4px 8px; border: 1px solid {t['border_default']}; }}"
            )
        # 更新告警边框颜色（非告警状态也同步主题边框色）
        self._cached_alert_border = None  # 强制下次刷新
        if not self._alert_active_global:
            self._cached_alert_border = t['border_default']
            self.plot_widget.setStyleSheet(
                f"PlotWidget {{ border: 2px solid {t['border_default']}; border-radius: 4px; }}"
            )
        # 散点图控件组边框同步主题
        self.scatter_frame.setStyleSheet(
            f"QFrame {{ border: 1px solid {t['border_default']}; border-radius: 4px; padding: 2px 4px; }}"
        )

    # --- 字段管理 ---
    def add_field(self, path: str, alias: str = "", is_computed: bool = False, expression: str = ""):
        if path in self._tracked:
            return False

        color = CURVE_COLORS[self._color_idx % len(CURVE_COLORS)]
        self._color_idx += 1

        display_name = alias or path
        # 计算字段在 chip 标签上显示 fx 前缀
        chip_label = f"fx: {display_name}" if is_computed else display_name
        chip = TrackChip(path, color, chip_label)
        chip.removed.connect(self._on_chip_removed)
        chip.threshold_changed.connect(self._update_threshold_lines)

        # 插入到输入框之前
        self.chips_layout.insertWidget(self.chips_layout.count() - 2, chip)

        # 创建曲线
        r, g, b = color
        pen = pg.mkPen(color=(r, g, b), width=1.5)
        # 计算字段用虚线以示区分
        if is_computed:
            pen = pg.mkPen(color=(r, g, b), width=1.5, style=Qt.DashLine)
        curve = self.plot_widget.plot([], [], pen=pen, name=display_name)

        self._tracked[path] = {
            'chip': chip,
            'curve': curve,
            'buffer': deque(maxlen=MAX_CURVE_POINTS),
            'alias': alias or path,
            'color': color,
            'is_computed': is_computed,
            'expression': expression,
        }

        self.empty_text.setVisible(False)
        # 有字段了，启动刷新定时器
        if not self.refresh_timer.isActive():
            self.refresh_timer.start()

        # Feature 5: 更新散点图字段下拉框（批量恢复时跳过，由外部统一刷新）
        if not getattr(self, '_batch_restoring', False):
            self._update_scatter_combos()
            if self._chart_mode == 'scatter':
                self._ensure_scatter_items()
        return True

    def remove_field(self, path: str):
        if path not in self._tracked:
            return
        entry = self._tracked.pop(path)
        entry['chip'].setParent(None)
        self.plot_widget.removeItem(entry['curve'])
        # 清理阈值线
        for key in ('upper_line', 'lower_line'):
            if key in entry and entry[key] is not None:
                self.plot_widget.removeItem(entry[key])
        # 清理告警状态
        if path in self._alerts:
            del self._alerts[path]
        if not self._tracked:
            self.empty_text.setVisible(True)
            self.refresh_timer.stop()  # 无字段，停止刷新省资源
        # Feature 5: 更新散点图下拉框 + 散点项
        self._update_scatter_combos()
        if self._chart_mode == 'scatter':
            self._ensure_scatter_items()
        # 检查全局告警是否已清除
        was_active = self._alert_active_global
        self._alert_active_global = any(a['active'] for a in self._alerts.values())
        if was_active != self._alert_active_global:
            self.alert_state_changed.emit(self._alert_active_global)

    def feed_data(self, obj: dict):
        """从新 JSON 对象中提取跟踪字段的值，含告警检测和计算字段求值"""
        if not hasattr(self, '_tracked') or self._paused or not self._tracked:
            return

        # ── Phase 1: 收集所有非计算字段的当前值 ──
        field_values = {}
        for path, entry in self._tracked.items():
            if entry.get('is_computed'):
                continue
            try:
                val = _extract_json_path(obj, path)
                if val is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
                    field_values[path] = float(val)
            except Exception:
                pass

        # ── Phase 2: 写入 buffer（非计算字段从 JSON 取值，计算字段从表达式求值）──
        self._point_index += 1
        x = self._point_index

        for path, entry in self._tracked.items():
            try:
                if entry.get('is_computed'):
                    val = eval_computed(entry['expression'], field_values, obj)
                    if val is not None:
                        # 将计算字段的值也加入 field_values，供后续计算字段引用
                        field_values[path] = val
                else:
                    val = field_values.get(path)

                if val is not None and isinstance(val, (int, float)):
                    entry['buffer'].append((x, val))
                    # Feature 2: 告警检测
                    self._check_alerts(path, val)
            except Exception:
                pass

    def _update_threshold_lines(self):
        """更新阈值线（简化版：移除旧的，添加新的）"""
        for entry in self._tracked.values():
            chip = entry['chip']
            # 移除旧的阈值线
            for key in ('upper_line', 'lower_line'):
                if key in entry and entry[key] is not None:
                    self.plot_widget.removeItem(entry[key])
                    entry[key] = None

            color = entry['color']
            if chip.threshold_upper is not None:
                line = pg.InfiniteLine(
                    pos=chip.threshold_upper, angle=0,
                    pen=pg.mkPen(color=color, width=1, style=Qt.DashLine)
                )
                self.plot_widget.addItem(line)
                entry['upper_line'] = line
            if chip.threshold_lower is not None:
                line = pg.InfiniteLine(
                    pos=chip.threshold_lower, angle=0,
                    pen=pg.mkPen(color=color, width=1, style=Qt.DashLine)
                )
                self.plot_widget.addItem(line)
                entry['lower_line'] = line

    def clear_all_data(self):
        if not hasattr(self, '_tracked'):
            return
        for entry in self._tracked.values():
            entry['buffer'].clear()
            entry['curve'].setData([], [])
        self._point_index = 0
        # 清空多字段散点项
        for item in self._scatter_items.values():
            item.setData([], [])
        # 隐藏统计浮层
        self.stats_overlay.hide()

    # --- Feature 2: 告警检测 ---
    def _check_alerts(self, path: str, value: float):
        """检测阈值越界并更新告警状态"""
        entry = self._tracked.get(path)
        if not entry:
            return
        chip = entry['chip']
        triggered = False
        if chip.threshold_upper is not None and value > chip.threshold_upper:
            triggered = True
        if chip.threshold_lower is not None and value < chip.threshold_lower:
            triggered = True

        if path not in self._alerts:
            self._alerts[path] = {'active': False, 'count': 0, 'last_trigger': 0}

        alert = self._alerts[path]
        if triggered and not alert['active']:
            alert['active'] = True
            alert['count'] += 1
            alert['last_trigger'] = time.time()
            # 给 chip 加红色边框
            chip.setStyleSheet(
                "QWidget { border: 2px solid #E06C75; border-radius: 4px; }"
            )
        elif not triggered and alert['active']:
            alert['active'] = False
            # 清除 chip 红色边框
            chip.setStyleSheet("")

        # 检查全局告警状态
        was_active = self._alert_active_global
        self._alert_active_global = any(a['active'] for a in self._alerts.values())
        if was_active != self._alert_active_global:
            self.alert_state_changed.emit(self._alert_active_global)

    def _update_stats_overlay(self):
        """更新图表右上角统计摘要（当前值 [min~max] 均值）"""
        lines = []
        for path, entry in self._tracked.items():
            buf = entry.get('buffer', deque())
            if not buf:
                continue
            alias = entry.get('alias', path) or path
            stats = _compute_stats(buf)
            if not stats:
                continue
            line = (
                f"{alias[:6]:6s} {stats['current']:>7.2f} "
                f"[{stats['min']:.1f}~{stats['max']:.1f}] "
                f"avg {stats['mean']:.2f}"
            )
            lines.append(line)
        if lines:
            self.stats_overlay.setText('\n'.join(lines))
            self.stats_overlay.adjustSize()
            self.stats_overlay.show()
            self.stats_overlay.raise_()  # 确保在 pyqtgraph 绘制层之上
            # 定位到右上角
            pw = self.plot_widget
            self.stats_overlay.move(
                pw.width() - self.stats_overlay.width() - 6, 6
            )
        else:
            self.stats_overlay.hide()

    def _update_alert_style(self):
        """根据告警状态更新图表边框（仅状态变化时设置 QSS）"""
        if not hasattr(self, 'plot_widget'):
            return
        t = _VIEWER_TOKENS['dark' if self._is_dark else 'light']
        alert_border = t['danger'] if self._alert_active_global else t['border_default']
        cached = getattr(self, '_cached_alert_border', None)
        if cached == alert_border:
            return
        self._cached_alert_border = alert_border
        self.plot_widget.setStyleSheet(
            f"PlotWidget {{ border: 2px solid {alert_border}; border-radius: 4px; }}"
        )

    def get_alert_count(self) -> int:
        """返回当前活跃告警数量和总触发次数"""
        active = sum(1 for a in self._alerts.values() if a['active'])
        total = sum(a['count'] for a in self._alerts.values())
        return active, total

    # --- Feature 5: 散点图模式 ---
    def _toggle_chart_mode(self):
        """切换折线图 / 散点图模式"""
        if self._chart_mode == 'line':
            self._chart_mode = 'scatter'
            self.btn_scatter_mode.setText("切换折线图")
            self.btn_scatter_mode.setChecked(True)
            # 隐藏所有折线
            for entry in self._tracked.values():
                entry['curve'].hide()
            # 显示散点控件组
            self.scatter_frame.show()
            self.plot_widget.setLabel('bottom', '')
            self._update_scatter_combos()
            self._ensure_scatter_items()
            self._update_scatter()
            self.plot_widget.autoRange()
            self.plot_widget.getPlotItem().vb.enableAutoRange()
        else:
            self._chart_mode = 'line'
            self.btn_scatter_mode.setText("切换散点图")
            self.btn_scatter_mode.setChecked(False)
            # 隐藏散点控件组
            self.scatter_frame.hide()
            self.scatter_curve.hide()
            self.scatter_curve.setData([], [])
            self._hide_all_scatter_items()
            self.regression_line.hide()
            self.regression_line.setData([], [])
            self.plot_widget.setLabel('bottom', '采样点')
            for entry in self._tracked.values():
                entry['curve'].show()
            self._update_plot()
            self.plot_widget.autoRange()
            self.plot_widget.getPlotItem().vb.enableAutoRange()

    def _update_scatter_combos(self):
        """更新散点图 X/Y 字段下拉框"""
        paths = list(self._tracked.keys())
        aliases = [self._tracked[p].get('alias', p) or p for p in paths]

        self.combo_x_field.blockSignals(True)
        self.combo_y_field.blockSignals(True)

        self.combo_x_field.clear()
        self.combo_y_field.clear()

        # X 轴首项：采样序号（单字段时默认用此项）
        self.combo_x_field.addItem("采样序号", "__time__")
        for path, alias in zip(paths, aliases):
            self.combo_x_field.addItem(alias, path)
            self.combo_y_field.addItem(alias, path)

        # 恢复上次选中
        x_idx = self.combo_x_field.findData(self._scatter_x_path) if self._scatter_x_path else -1
        y_idx = self.combo_y_field.findData(self._scatter_y_path) if self._scatter_y_path else -1

        # 多字段时，如果「采样序号」是自动默认值（非用户手动选），重置为真实字段
        single_field = (len(paths) == 1)
        if not single_field and x_idx == 0 and not self._scatter_manual:
            x_idx = -1

        # 默认值：1个字段 → X=采样序号；2+字段 → X=字段1, Y=字段2
        if x_idx < 0:
            x_idx = 0 if single_field else 1
            self._scatter_manual = False
        if y_idx < 0:
            y_idx = 0
            self._scatter_manual = False

        # 如果 X=Y（同一个真实字段），Y 换到不同字段
        x_data = self.combo_x_field.itemData(x_idx)
        y_data = self.combo_y_field.itemData(y_idx)
        if x_data != '__time__' and x_data == y_data and len(paths) >= 2:
            y_idx = 1 if y_idx == 0 else 0

        self.combo_x_field.setCurrentIndex(x_idx)
        self.combo_y_field.setCurrentIndex(y_idx)
        self._scatter_x_path = self.combo_x_field.itemData(x_idx)
        self._scatter_y_path = self.combo_y_field.itemData(y_idx)

        # Y 在 X=采样序号时无效（显示所有字段）
        is_time = (self._scatter_x_path == '__time__')
        self.lbl_y_field.setEnabled(not is_time)
        self.combo_y_field.setEnabled(not is_time)

        self.combo_x_field.blockSignals(False)
        self.combo_y_field.blockSignals(False)

    def _on_scatter_field_changed(self):
        self._scatter_x_path = self.combo_x_field.currentData()
        self._scatter_y_path = self.combo_y_field.currentData()
        self._scatter_manual = True   # 用户主动选择，后续恢复时保留
        # X=采样序号时 Y 无效（显示所有字段），禁用 Y 下拉框
        self.lbl_y_field.setEnabled(self._scatter_x_path != '__time__')
        self.combo_y_field.setEnabled(self._scatter_x_path != '__time__')
        self._update_scatter()

    def _update_scatter(self):
        """更新散点图：X=采样序号 → 多字段各色散点；X=字段 → 字段-vs-字段相关性"""
        if self._chart_mode != 'scatter':
            return
        x_path = self._scatter_x_path or self.combo_x_field.currentData()
        y_path = self._scatter_y_path or self.combo_y_field.currentData()
        if not x_path or not y_path:
            return

        if x_path == '__time__':
            # ── 模式A：采样序号 vs 所有字段（每字段独立颜色）──
            self.scatter_curve.hide()
            self.regression_line.hide()
            self._ensure_scatter_items()  # 确保创建 + 显示（切换到该模式时）
            plot_width = self.plot_widget.width()

            for path, entry in self._tracked.items():
                buf = entry.get('buffer', deque())
                if not buf:
                    continue
                if len(buf) > plot_width:
                    pts = lttb_downsample(list(buf), plot_width)
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                else:
                    xs = [p[0] for p in buf]
                    ys = [p[1] for p in buf]
                item = self._scatter_items.get(path)
                if item:
                    item.setData(xs, ys)
                    item.show()  # 确保可见（从相关性模式切回时可能隐藏）

            self.plot_widget.setLabel('bottom', '采样序号')
        else:
            # ── 模式B：字段 vs 字段（单一颜色 + 回归线）──
            self._hide_all_scatter_items()
            self.scatter_curve.show()

            y_entry = self._tracked.get(y_path)
            x_entry = self._tracked.get(x_path)
            if not x_entry or not y_entry:
                return
            x_buf = x_entry.get('buffer', deque())
            y_buf = y_entry.get('buffer', deque())
            if not x_buf or not y_buf:
                return

            x_map = {p[0]: p[1] for p in x_buf}
            y_map = {p[0]: p[1] for p in y_buf}
            indices = sorted(set(x_map.keys()) & set(y_map.keys()))[-500:]
            if not indices:
                return
            xs = [x_map[i] for i in indices]
            ys = [y_map[i] for i in indices]

            plot_width = self.plot_widget.width()
            if len(xs) > plot_width:
                pts = lttb_downsample(list(zip(xs, ys)), plot_width)
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]

            self.scatter_curve.setData(xs, ys)

            reg = _linear_regression(xs, ys)
            if reg and len(xs) >= 3:
                a, b, r2 = reg
                x_min, x_max = min(xs), max(xs)
                reg_x = [x_min, x_max]
                reg_y = [a + b * x_min, a + b * x_max]
                self.regression_line.setData(reg_x, reg_y)
                self.regression_line.show()
                x_alias = self._tracked[x_path].get('alias', x_path) or x_path
                y_alias = self._tracked[y_path].get('alias', y_path) or y_path
                self.plot_widget.setLabel(
                    'bottom',
                    f'{x_alias} vs {y_alias}  (R²={r2:.4f}, y={a:.4f}+{b:.4f}x)'
                )
            else:
                self.regression_line.hide()

    def _ensure_scatter_items(self):
        """确保每个跟踪字段有对应颜色的散点项，并全部显示"""
        for path, entry in self._tracked.items():
            if path in self._scatter_items:
                self._scatter_items[path].show()
                continue
            r, g, b = entry['color']
            item = pg.ScatterPlotItem(
                size=8, pen=pg.mkPen(None),
                brush=pg.mkBrush(r, g, b, 180)
            )
            self.plot_widget.addItem(item)
            self._scatter_items[path] = item
        # 清理已移除字段的散点项
        for path in list(self._scatter_items.keys()):
            if path not in self._tracked:
                self.plot_widget.removeItem(self._scatter_items.pop(path))

    def _hide_all_scatter_items(self):
        """隐藏所有多字段散点项"""
        for item in self._scatter_items.values():
            item.hide()
            item.setData([], [])

    # --- Feature 3: 计算字段 ---
    def _add_computed_field(self):
        """弹出对话框添加计算字段"""
        text, ok = QInputDialog.getText(
            self, "添加计算字段",
            "输入表达式 (如: volt * 100):\n"
            "引用格式: field_name 或 data.volt（点号路径）\n"
            "支持: + - * / ** % ( )\n\n"
            "别名 (可选，分号分隔，不填则用表达式自身):\n"
            "例: temperature * 9/5 + 32 ; 华氏温度"
        )
        if not ok or not text:
            return

        # 解析别名
        parts = text.split(';')
        expr = parts[0].strip()
        alias = parts[1].strip() if len(parts) > 1 else ""

        if not expr:
            return

        # 无别名时用表达式自身作为显示名（去除空格，超过24字符截断）
        if not alias:
            alias = expr.replace(' ', '')
            if len(alias) > 24:
                alias = alias[:21] + '...'

        # 生成唯一路径（内部使用，不暴露给用户）
        import uuid
        computed_path = f"__computed__{uuid.uuid4().hex[:8]}"

        self.add_field(computed_path, alias=alias, is_computed=True, expression=expr)

    # --- 内部控制 ---
    def _toggle_pause(self):
        self._paused = self.btn_pause.isChecked()
        self.btn_pause.setText("继续" if self._paused else "暂停")

    def _on_chip_removed(self, chip):
        self.remove_field(chip.path)

    def _on_add_field(self):
        path = self.edit_add_field.text().strip()
        if path:
            self.add_field(path)
            self.edit_add_field.clear()

    def _update_plot(self):
        if self._paused or not hasattr(self, '_tracked'):
            return

        # 更新右上角统计摘要
        self._update_stats_overlay()

        # Feature 2: 更新告警边框样式
        self._update_alert_style()

        if self._chart_mode == 'scatter':
            self._update_scatter()
            return

        # 折线图模式：更新每条曲线
        plot_width = self.plot_widget.width()

        for entry in self._tracked.values():
            buf = entry['buffer']
            if not buf:
                continue
            if len(buf) > plot_width:
                pts = lttb_downsample(list(buf), plot_width)
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
            else:
                xs = [p[0] for p in buf]
                ys = [p[1] for p in buf]
            entry['curve'].setData(xs, ys)

    def _on_mouse_moved(self, evt):
        pos = evt[0]
        if self.plot_widget.sceneBoundingRect().contains(pos):
            mouse_pt = self.plot_widget.getPlotItem().vb.mapSceneToView(pos)
            x, y = mouse_pt.x(), mouse_pt.y()

            # 查找最近数据点
            min_dist = float('inf')
            tooltip = ""
            for path, entry in self._tracked.items():
                buf = entry['buffer']
                if not buf:
                    continue
                # 二分查最近 x
                pts = list(buf)
                nearest = min(pts, key=lambda p: abs(p[0] - x))
                dist = abs(nearest[0] - x)
                if dist < 50:  # 50 像素阈值
                    tooltip += f"{entry['alias']}: {nearest[1]:.4f}\n"

            if tooltip:
                self.crosshair_v.setPos(x)
                self.crosshair_h.setPos(y)
                self.crosshair_v.show()
                self.crosshair_h.show()
                QToolTip.showText(QCursor.pos(), tooltip.strip(), self)
            else:
                self.crosshair_v.hide()
                self.crosshair_h.hide()

    # --- 导出 ---
    def _export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出 PNG", "chart_export.png", "PNG (*.png)")
        if path:
            pix = self.plot_widget.grab()
            pix.save(path, 'PNG')

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", "track_data.csv", "CSV (*.csv)")
        if not path:
            return
        import csv
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            # 构建所有字段的时间序列
            all_x = set()
            for entry in self._tracked.values():
                for p in entry['buffer']:
                    all_x.add(p[0])
            all_x = sorted(all_x)

            headers = ['index'] + [e['alias'] for e in self._tracked.values()]
            w.writerow(headers)

            # 构建 x -> values map
            data_maps = {}
            for path, entry in self._tracked.items():
                data_maps[path] = {p[0]: p[1] for p in entry['buffer']}

            paths = list(self._tracked.keys())
            for x in all_x:
                row = [x]
                for p in paths:
                    row.append(data_maps[p].get(x, ''))
                w.writerow(row)

    # --- 拖放支持（ChartTrackerWidget + PlotWidget 事件过滤器）---
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/x-json-path'):
            event.acceptProposedAction()

    def dropEvent(self, event):
        data = event.mimeData().data('application/x-json-path')
        path = bytes(data).decode('utf-8')
        self.add_field(path)
        event.acceptProposedAction()

    def eventFilter(self, obj, event):
        """将 PlotWidget 的拖放/调整大小事件转发给 ChartTrackerWidget"""
        if obj is self.plot_widget:
            if event.type() == QEvent.Resize:
                # 调整大小时重新定位统计浮层
                if hasattr(self, 'stats_overlay') and self.stats_overlay.isVisible():
                    self.stats_overlay.move(
                        self.plot_widget.width() - self.stats_overlay.width() - 6, 6
                    )
            elif event.type() == QEvent.DragEnter:
                if event.mimeData().hasFormat('application/x-json-path'):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.DragMove:
                if event.mimeData().hasFormat('application/x-json-path'):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Drop:
                data = event.mimeData().data('application/x-json-path')
                path = bytes(data).decode('utf-8')
                self.add_field(path)
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)


# ──────────────────────────────────────────────
#  统计摘要面板（Feature 1）
# ──────────────────────────────────────────────
class StatsPanelWidget(QWidget):
    """统计摘要面板 — 实时显示所有跟踪字段的 min/max/mean/std 等统计量"""

    COLUMNS = ["字段", "当前值", "最小值", "最大值", "平均值", "标准差", "数量", "变化量"]

    def __init__(self, chart_tracker, parent=None):
        super().__init__(parent)
        self._chart_tracker = chart_tracker
        self._is_dark = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 提示标签
        self.lbl_hint = QLabel("暂无跟踪字段 — 在下方图表区添加字段后自动显示统计")
        self.lbl_hint.setFont(QFont("Microsoft YaHei", 9))
        self.lbl_hint.setAlignment(Qt.AlignCenter)

        # 统计表格
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._apply_table_style(True)

        layout.addWidget(self.lbl_hint)
        layout.addWidget(self.table)

        self.refresh()

    def set_theme(self, is_dark: bool):
        self._is_dark = is_dark
        self._apply_table_style(is_dark)

    def _apply_table_style(self, is_dark: bool):
        self._is_dark = is_dark
        t = _VIEWER_TOKENS['dark' if is_dark else 'light']
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {t['bg_card']};
                color: {t['text_primary']};
                border: 1px solid {t['border_default']};
                gridline-color: {t['bg_card']};
                selection-background-color: {t['selection_bg']};
                selection-color: {t['selection_fg']};
                alternate-background-color: {t['bg_alt']};
            }}
            QTableWidget::item {{ padding: 3px 8px; }}
            QHeaderView {{ background-color: {t['bg_header']}; color: {t['text_secondary']}; border: none; }}
            QHeaderView::section {{
                background-color: {t['bg_header']}; color: {t['text_secondary']};
                border: 1px solid {t['border_light']}; padding: 4px 6px; font-weight: bold;
            }}
        """)

    def refresh(self):
        tracked = self._chart_tracker._tracked
        if not tracked:
            self.lbl_hint.show()
            self.table.setRowCount(0)
            return

        self.lbl_hint.hide()
        paths = list(tracked.keys())
        self.table.setRowCount(len(paths))

        for r, path in enumerate(paths):
            entry = tracked[path]
            alias = entry.get('alias', path) or path
            buf = entry.get('buffer', deque())
            stats = _compute_stats(buf)

            items = [
                alias,
                f"{stats['current']:.4f}" if stats else "—",
                f"{stats['min']:.4f}" if stats else "—",
                f"{stats['max']:.4f}" if stats else "—",
                f"{stats['mean']:.4f}" if stats else "—",
                f"{stats['std']:.4f}" if stats else "—",
                str(stats['count']) if stats else "0",
                f"{stats['delta']:+.4f}" if stats else "—",
            ]
            for c, val in enumerate(items):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                # 变化量为正显示绿色，为负显示红色
                if c == 7 and stats:
                    try:
                        if stats['delta'] > 0:
                            item.setForeground(QColor(0x98, 0xC3, 0x79))
                        elif stats['delta'] < 0:
                            item.setForeground(QColor(0xE0, 0x6C, 0x75))
                    except Exception:
                        pass
                self.table.setItem(r, c, item)

        self.table.resizeColumnsToContents()


# ──────────────────────────────────────────────
#  数据表格视图（Feature 4）
# ──────────────────────────────────────────────
class DataTableWidget(QWidget):
    """实时数据表格 — 显示最近 N 条跟踪字段的原始数值"""

    DEFAULT_ROWS = 50

    def __init__(self, chart_tracker, parent=None, is_dark=True):
        super().__init__(parent)
        self._chart_tracker = chart_tracker
        self._is_dark = is_dark

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── 工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        lbl_title = QLabel("── 数据表格 ──")
        lbl_title.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        toolbar.addWidget(lbl_title)

        toolbar.addStretch()

        toolbar.addWidget(QLabel("行数:", font=QFont("Microsoft YaHei", 9)))
        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(10, 500)
        self.spin_rows.setValue(self.DEFAULT_ROWS)
        self.spin_rows.setFont(QFont("Consolas", 9))
        self.spin_rows.setMaximumWidth(70)
        toolbar.addWidget(self.spin_rows)

        self.check_autoscroll = QCheckBox("自动滚动")
        self.check_autoscroll.setFont(QFont("Microsoft YaHei", 9))
        self.check_autoscroll.setChecked(True)
        toolbar.addWidget(self.check_autoscroll)

        layout.addLayout(toolbar)

        # ── 表格 ──
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._apply_table_style(is_dark)

        # 空状态提示
        self.table.setRowCount(1)
        self.table.setColumnCount(1)
        self.table.setHorizontalHeaderLabels([""])
        hint = QTableWidgetItem("暂无数据 — 在图表区添加跟踪字段后自动填充")
        hint.setTextAlignment(Qt.AlignCenter)
        hint.setForeground(QColor(0x88, 0x88, 0x88))
        self.table.setItem(0, 0, hint)

        layout.addWidget(self.table, stretch=1)

    def set_theme(self, is_dark: bool):
        self._is_dark = is_dark
        self._apply_table_style(is_dark)

    def _apply_table_style(self, is_dark: bool):
        self._is_dark = is_dark
        t = _VIEWER_TOKENS['dark' if is_dark else 'light']
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {t['bg_card']};
                color: {t['text_primary']};
                border: 1px solid {t['border_default']};
                gridline-color: {t['bg_card']};
                selection-background-color: {t['selection_bg']};
                selection-color: {t['selection_fg']};
                alternate-background-color: {t['bg_alt']};
            }}
            QTableWidget::item {{ padding: 2px 6px; }}
            QHeaderView {{ background-color: {t['bg_header']}; color: {t['text_secondary']}; border: none; }}
            QHeaderView::section {{
                background-color: {t['bg_header']}; color: {t['text_secondary']};
                border: 1px solid {t['border_light']}; padding: 3px 6px; font-weight: bold;
            }}
        """)

    def refresh(self):
        tracked = self._chart_tracker._tracked
        if not tracked:
            self.table.setRowCount(1)
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderLabels([""])
            hint = QTableWidgetItem("暂无数据")
            hint.setTextAlignment(Qt.AlignCenter)
            hint.setForeground(QColor(0x88, 0x88, 0x88))
            self.table.setItem(0, 0, hint)
            return

        col_paths = list(tracked.keys())
        col_aliases = [tracked[p].get('alias', p) or p for p in col_paths]

        # 收集所有 x 坐标并取最近 N 个
        all_x = sorted(set(x for entry in tracked.values() for x, _ in entry.get('buffer', deque())))
        max_rows = self.spin_rows.value()
        recent_x = all_x[-max_rows:] if all_x else []

        # 构建每个字段的 x→val 映射
        field_maps = {}
        for path in col_paths:
            buf = tracked[path].get('buffer', deque())
            field_maps[path] = {p[0]: p[1] for p in buf}

        self.table.setColumnCount(len(col_paths))
        self.table.setHorizontalHeaderLabels(col_aliases)
        self.table.setRowCount(len(recent_x))

        for r, x in enumerate(recent_x):
            for c, path in enumerate(col_paths):
                val = field_maps[path].get(x, None)
                text = f"{val:.4f}" if val is not None else "—"
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, item)

        if self.check_autoscroll.isChecked():
            self.table.scrollToBottom()

    def clear_all(self):
        self.table.setRowCount(1)
        self.table.setColumnCount(1)
        self.table.setHorizontalHeaderLabels([""])
        hint = QTableWidgetItem("暂无数据")
        hint.setTextAlignment(Qt.AlignCenter)
        hint.setForeground(QColor(0x88, 0x88, 0x88))
        self.table.setItem(0, 0, hint)


# ──────────────────────────────────────────────
#  JSON 详情查看器
# ──────────────────────────────────────────────
class DetailViewerWidget(QWidget):
    """JSON 详情查看器：树形 / 表格 / 原始文本 / 统计 四视图切换"""

    def __init__(self, parent=None, chart_tracker=None):
        super().__init__(parent)
        self._current_obj = None
        self._current_raw = ""
        self._current_path = ""  # 当前查看项的路径
        self._chart_tracker = chart_tracker  # 外部图表跟踪器引用
        self._export_all_callback = None     # 导出全部回调，由外部注入

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── 工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        self.lbl_current = QLabel("当前查看: —")
        self.lbl_current.setFont(QFont("Microsoft YaHei", 9))
        toolbar.addWidget(self.lbl_current)

        toolbar.addStretch()

        toolbar.addWidget(QLabel("视图:"))
        self.combo_view = QComboBox()
        self.combo_view.addItems(["树形", "表格", "原始文本", "统计"])
        self.combo_view.setFont(QFont("Microsoft YaHei", 9))
        self.combo_view.currentIndexChanged.connect(self._on_view_changed)
        toolbar.addWidget(self.combo_view)

        layout.addLayout(toolbar)

        # ── 视图栈 ──
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        # 树形视图（支持拖拽 JSON 路径到图表区）
        self.tree_view = _DragTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["键", "值"])
        # 初始空状态引导提示
        hint_key = QStandardItem("← 点击左侧列表项查看详情")
        hint_key.setForeground(QColor(0x88, 0x88, 0x88))
        hint_key.setEditable(False)
        hint_val = QStandardItem("树形/表格/原始文本/统计 四视图切换")
        hint_val.setForeground(QColor(0x88, 0x88, 0x88))
        hint_val.setEditable(False)
        self.tree_model.appendRow([hint_key, hint_val])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setColumnWidth(0, 220)
        self.tree_view.header().setStretchLastSection(True)
        self.tree_view.setExpandsOnDoubleClick(True)
        self.tree_view.setAnimated(True)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.stack.addWidget(self.tree_view)

        # 表格视图
        self.table_view = QTableWidget()
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self._on_table_context_menu)
        self.stack.addWidget(self.table_view)

        # 原始文本视图
        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setFont(QFont("Consolas", 10))
        self.raw_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.highlighter = JsonSyntaxHighlighter(self.raw_view.document(), is_dark=True)
        self.stack.addWidget(self.raw_view)

        # 统计面板（Feature 1 — 作为第4页，由外部 chart_tracker 驱动）
        self.stats_panel = StatsPanelWidget(chart_tracker, parent=self)
        self.stack.addWidget(self.stats_panel)

        # ── 操作按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        btn_copy = QPushButton("复制值")
        btn_copy.setFont(QFont("Microsoft YaHei", 9))
        btn_copy.clicked.connect(self._copy_value)
        btn_row.addWidget(btn_copy)

        btn_export_all = QPushButton("导出全部 JSON")
        btn_export_all.setFont(QFont("Microsoft YaHei", 9))
        btn_export_all.clicked.connect(self._export_all)
        btn_row.addWidget(btn_export_all)

        btn_add_chart = QPushButton("添加到图表")
        btn_add_chart.setFont(QFont("Microsoft YaHei", 9))
        btn_add_chart.clicked.connect(self._add_to_chart)
        btn_row.addWidget(btn_add_chart)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    # --- 设置数据 ---
    def set_data(self, raw_text: str, obj: Optional[dict], seq: int = 0):
        self._current_obj = obj
        self._current_raw = raw_text
        self.lbl_current.setText(f"当前查看: 第 {seq} 条")

        # 树形
        self.tree_model.removeRows(0, self.tree_model.rowCount())
        if obj is not None:
            root = _build_json_tree(obj)
            while root.rowCount() > 0:
                row_items = root.takeRow(0)
                self.tree_model.appendRow(row_items)
            self.tree_view.expandToDepth(0)
        else:
            # 非 JSON 数据：树形中显示提示 + 全文预览
            hint = QStandardItem("(非JSON数据，无可拖拽字段 — 可切换「原始文本」/「表格」查看全文)")
            hint.setForeground(QColor(0x88, 0x88, 0x88))
            hint.setEditable(False)
            preview = raw_text[:300] + ("…" if len(raw_text) > 300 else "")
            self.tree_model.appendRow([hint, QStandardItem(preview)])

        # 表格：JSON → 结构化展示；非JSON → 尝试提取 key=value 对
        if obj is not None:
            self._build_table(obj)
        else:
            kv = _extract_kv_pairs(raw_text)
            if kv:
                self.table_view.clear()
                self.table_view.setColumnCount(2)
                self.table_view.setHorizontalHeaderLabels(["字段", "值"])
                self.table_view.setRowCount(len(kv))
                for r, (k, v) in enumerate(kv.items()):
                    self.table_view.setItem(r, 0, QTableWidgetItem(str(k)))
                    self.table_view.setItem(r, 1, QTableWidgetItem(str(v)))
            else:
                self.table_view.clear()
                self.table_view.setRowCount(1)
                self.table_view.setColumnCount(1)
                self.table_view.setHorizontalHeaderLabels(["原始文本"])
                item = QTableWidgetItem(raw_text)
                item.setFont(QFont("Consolas", 10))
                self.table_view.setItem(0, 0, item)

        # 原始文本：始终展示全文
        self.raw_view.setPlainText(raw_text)

    def _build_table(self, obj):
        """将 JSON 对象/数组扁平化为表格"""
        self.table_view.clear()
        self.table_view.setRowCount(0)

        if obj is None:
            return

        if isinstance(obj, list) and len(obj) > 0 and all(isinstance(x, dict) for x in obj):
            # 数组对象展平
            flat = [_flatten_dict(item) for item in obj]
            all_keys = []
            for item in flat:
                for k in item:
                    if k not in all_keys:
                        all_keys.append(k)

            self.table_view.setColumnCount(len(all_keys))
            self.table_view.setHorizontalHeaderLabels(all_keys)
            self.table_view.setRowCount(len(flat))
            for r, item in enumerate(flat):
                for c, key in enumerate(all_keys):
                    val = item.get(key, "")
                    self.table_view.setItem(r, c, QTableWidgetItem(str(val)))
        elif isinstance(obj, dict):
            # 单对象：键值对表格
            self.table_view.setColumnCount(2)
            self.table_view.setHorizontalHeaderLabels(["键", "值"])
            items = list(obj.items())
            self.table_view.setRowCount(len(items))
            for r, (k, v) in enumerate(items):
                self.table_view.setItem(r, 0, QTableWidgetItem(str(k)))
                if isinstance(v, (dict, list)):
                    self.table_view.setItem(r, 1, QTableWidgetItem(json.dumps(v, ensure_ascii=False)[:200]))
                else:
                    self.table_view.setItem(r, 1, QTableWidgetItem(str(v)))

    # --- 视图切换 ---
    def _on_view_changed(self, idx):
        self.stack.setCurrentIndex(idx)

    # --- 右键菜单 ---
    def _on_tree_context_menu(self, pos: QPoint):
        idx = self.tree_view.indexAt(pos)
        if not idx.isValid():
            return
        item = self.tree_model.itemFromIndex(idx)
        if item is None:
            return
        path = item.data(Qt.UserRole) or ""
        val = item.text()

        menu = QMenu(self)
        act_copy = menu.addAction(f"复制值: {val[:50]}")
        act_copy_path = menu.addAction(f"复制路径: {path}")
        menu.addSeparator()
        act_chart = menu.addAction("添加到图表跟踪")

        action = menu.exec_(self.tree_view.viewport().mapToGlobal(pos))
        if action == act_copy:
            QApplication.clipboard().setText(val)
        elif action == act_copy_path:
            QApplication.clipboard().setText(path)
        elif action == act_chart:
            self._add_to_chart(path)

    def _on_table_context_menu(self, pos: QPoint):
        item = self.table_view.itemAt(pos)
        if item is None:
            return
        val = item.text()
        menu = QMenu(self)
        act_copy = menu.addAction(f"复制: {val[:50]}")
        action = menu.exec_(self.table_view.viewport().mapToGlobal(pos))
        if action == act_copy:
            QApplication.clipboard().setText(val)

    # --- 按钮动作 ---
    def _copy_value(self):
        """复制当前选中节点的值"""
        # 根据当前视图决定
        if self.stack.currentIndex() == 0:  # 树形
            idx = self.tree_view.currentIndex()
            if idx.isValid():
                item = self.tree_model.itemFromIndex(idx)
                if item:
                    QApplication.clipboard().setText(item.text())
        elif self.stack.currentIndex() == 1:  # 表格
            item = self.table_view.currentItem()
            if item:
                QApplication.clipboard().setText(item.text())
        elif self.stack.currentIndex() == 2:  # 原始文本
            cursor = self.raw_view.textCursor()
            if cursor.hasSelection():
                QApplication.clipboard().setText(cursor.selectedText())

    def _export_all(self):
        """导出全部 JSONL（通过回调委托给上层，导出所有捕获条目）"""
        if self._export_all_callback:
            self._export_all_callback()
        else:
            # 回退：仅导出当前项
            path, _ = QFileDialog.getSaveFileName(self, "导出 JSONL", "export.jsonl", "JSONL (*.jsonl)")
            if path:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(self._current_raw)

    def _add_to_chart(self, path: str = None):
        """将当前路径添加到图表跟踪器"""
        if path is None:
            # 从当前树形选择获取路径
            idx = self.tree_view.currentIndex()
            if idx.isValid():
                item = self.tree_model.itemFromIndex(idx)
                if item:
                    path = item.data(Qt.UserRole)
        if path and self._chart_tracker:
            self._chart_tracker.add_field(path)

    # --- 拖拽支持（树形视图通过 QTreeView 内置 Drag） ---
    def get_tree_model(self):
        return self.tree_model

    def set_theme(self, is_dark: bool):
        """响应主题切换"""
        # 更新语法高亮
        self.highlighter._init_formats(is_dark)
        # 更新原始文本视图背景
        t = _VIEWER_TOKENS['dark' if is_dark else 'light']
        self.raw_view.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {t['bg_card']}; color: {t['text_primary']}; }}"
        )
        # 表格视图：修复表头左上角白色方块 + 统一配色
        self.table_view.setStyleSheet(
            f"QTableCornerButton::section {{"
            f"  background-color: {t['bg_header']}; border: 1px solid {t['border_default']};"
            f"}}"
            f"QHeaderView::section {{"
            f"  background-color: {t['bg_header']}; color: {t['text_primary']};"
            f"  padding: 4px 8px; border: 1px solid {t['border_default']};"
            f"}}"
        )
        # 统计面板主题
        if hasattr(self, 'stats_panel') and self.stats_panel:
            self.stats_panel.set_theme(is_dark)


class _DragTreeView(QTreeView):
    """支持拖拽 JSON 路径到图表跟踪器的自定义 QTreeView

    使用方法：按住鼠标左键选中树中的数值节点，拖到下方图表区释放。
    （需要数据是有效的 JSON 对象，非JSON行没有可拖拽字段）
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDragEnabled(True)
        # 设置拖拽起始距离，防止轻微移动就触发拖拽
        self.setDragDropOverwriteMode(False)

    def mimeTypes(self):
        return ['application/x-json-path', 'text/plain']

    def mimeData(self, indexes):
        """Qt 自动调用：收集拖拽数据"""
        mime = QMimeData()
        for idx in indexes:
            if not idx.isValid():
                continue
            item = self.model().itemFromIndex(idx)
            if item is None:
                continue
            path = item.data(Qt.UserRole) or ""
            if path and path != "$":
                mime.setData('application/x-json-path', path.encode('utf-8'))
                mime.setText(path)
                break
        return mime


# ──────────────────────────────────────────────
#  捕获列表面板（左侧）
# ──────────────────────────────────────────────
class CaptureListWidget(QWidget):
    """左侧捕获列表面板：TableView + 底栏 + 右键菜单"""

    item_selected = pyqtSignal(int)     # 选中条目序号（用于详情查看）

    def __init__(self, parent=None, is_dark=True):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── 标题 ──
        lbl = QLabel("── 捕获列表 ──")
        lbl.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        layout.addWidget(lbl)

        # ── 表格 ──
        self.model = CaptureTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 180)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # 固定表格样式：根据传入主题初始化，避免主题切换闪烁
        self._is_dark = is_dark
        self._apply_table_style(is_dark)
        layout.addWidget(self.table)

        # ── 底栏 ──
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self.check_autoscroll = QCheckBox("自动滚动")
        self.check_autoscroll.setFont(QFont("Microsoft YaHei", 9))
        self.check_autoscroll.setChecked(True)
        bottom.addWidget(self.check_autoscroll)

        self.lbl_stats = QLabel("共 0 条")
        self.lbl_stats.setFont(QFont("Consolas", 9))
        bottom.addWidget(self.lbl_stats)

        bottom.addStretch()
        layout.addLayout(bottom)

    def set_theme(self, is_dark: bool):
        """响应主题切换，更新表格配色"""
        self._is_dark = is_dark
        self._apply_table_style(is_dark)

    def _apply_table_style(self, is_dark=True):
        """根据主题应用表格样式，确保文字/背景对比度清晰"""
        self._is_dark = is_dark
        t = _VIEWER_TOKENS['dark' if is_dark else 'light']
        self.table.setStyleSheet(f"""
            QTableView {{
                background-color: {t['bg_card']};
                color: {t['text_primary']};
                border: 1px solid {t['border_default']};
                gridline-color: {t['bg_card']};
                selection-background-color: {t['selection_bg']};
                selection-color: {t['selection_fg']};
                alternate-background-color: {t['bg_alt']};
            }}
            QTableView::item {{ padding: 2px 6px; }}
            QTableView::item:hover {{ background-color: {t['hover_row']}; }}
            QTableView::item:selected {{ background-color: {t['selection_bg']}; color: {t['selection_fg']}; }}
            QHeaderView {{ background-color: {t['bg_header']}; color: {t['text_secondary']}; border: none; }}
            QHeaderView::section {{
                background-color: {t['bg_header']}; color: {t['text_secondary']};
                border: 1px solid {t['border_light']}; padding: 4px 6px; font-weight: bold;
            }}
            QHeaderView::section:hover {{ background-color: {t['hover_header']}; }}
        """)

    # --- 数据操作 ---
    def append_items(self, items: list[dict]):
        self.model.append_items(items)
        self.lbl_stats.setText(
            f"显示 {self.model.visible_count}/{self.model.total_count} 条"
            + ("（已筛选）" if self.model._filter_enabled else "")
        )
        if self.check_autoscroll.isChecked():
            self.table.scrollToBottom()

    def clear(self):
        self.model.clear()
        self.lbl_stats.setText("共 0 条")

    def set_filter(self, text: str):
        self.model.set_filter(text)
        self.lbl_stats.setText(
            f"显示 {self.model.visible_count}/{self.model.total_count} 条"
            + ("（已筛选）" if self.model._filter_enabled else "")
        )

    def get_selected_items(self) -> list[dict]:
        """返回当前选中行的 item 列表"""
        result = []
        for idx in self.table.selectionModel().selectedRows():
            item = self.model.get_item(idx.row())
            if item:
                result.append(item)
        return result

    def remove_selected(self):
        indices = [idx.row() for idx in self.table.selectionModel().selectedRows()]
        self.model.remove_rows(indices)
        self.lbl_stats.setText(
            f"显示 {self.model.visible_count}/{self.model.total_count} 条"
            + ("（已筛选）" if self.model._filter_enabled else "")
        )

    # --- 事件 ---
    def _on_selection_changed(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.item_selected.emit(rows[0].row())

    def _on_context_menu(self, pos: QPoint):
        menu = QMenu(self)
        act_del = menu.addAction("删除选中")
        act_compare = menu.addAction("比较差异（选2条）")
        menu.addSeparator()
        act_export_sel = menu.addAction("导出选中为 .json")
        act_export_all = menu.addAction("导出全部为 .jsonl")

        # 比较差异需要正好选中2条
        selected = self.table.selectionModel().selectedRows()
        act_compare.setEnabled(len(selected) == 2)

        action = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if action == act_del:
            self.remove_selected()
        elif action == act_compare:
            items = self.get_selected_items()
            if len(items) == 2:
                self._show_diff(items[0], items[1])
        elif action == act_export_sel:
            self._export_selected()
        elif action == act_export_all:
            self._export_all()

    def _show_diff(self, a, b):
        """比较两个 JSON 差异并显示"""
        obj_a = a.get('obj')
        obj_b = b.get('obj')
        if obj_a is None or obj_b is None:
            QMessageBox.information(self, "比较差异", "其中一个条目非有效 JSON，无法比较。")
            return

        # 简单深度对比
        diffs = _dict_diff(obj_a, obj_b)

        dlg = QDialog(self)
        dlg.setWindowTitle("JSON 差异比较")
        dlg.resize(600, 400)
        dl = QVBoxLayout(dlg)

        te = QPlainTextEdit()
        te.setReadOnly(True)
        te.setFont(QFont("Consolas", 10))
        if diffs:
            te.setPlainText('\n'.join(diffs))
        else:
            te.setPlainText("✓ 两个 JSON 对象完全相同。")
        dl.addWidget(te)

        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    def _export_selected(self):
        items = self.get_selected_items()
        if not items:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出选中 JSON", "selected.json", "JSON (*.json)")
        if path:
            objs = [it.get('obj') or it.get('raw', '') for it in items]
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(objs if len(objs) > 1 else objs[0], f, indent=2, ensure_ascii=False)

    def _export_all(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出全部 JSONL", "all_data.jsonl", "JSONL (*.jsonl)")
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                for item in self.model._items:
                    f.write(item.get('raw', '') + '\n')


# ──────────────────────────────────────────────
#  JSON 捕获线程
# ──────────────────────────────────────────────
class JsonCaptureThread(QThread):
    """后台线程：从 bytes 流中提取 JSON 对象或二进制帧"""

    MODE_LINE_BY_LINE = 0    # 逐行尝试解析
    MODE_JSON_OBJECT = 1     # 提取 JSON 对象（{...} 或 [...]）
    MODE_REGEX = 2           # 自定义正则
    MODE_BINARY = 3          # 二进制协议解析

    items_ready = pyqtSignal(list)  # [(raw_text, obj, summary, parse_error), ...]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False
        self.filter_mode = self.MODE_LINE_BY_LINE
        self.custom_regex = ""          # 自定义正则模式
        self.protocol_template: ProtocolTemplate | None = None  # 二进制协议模板
        self._queue: deque[bytes] = deque()
        self._queue_mutex = QMutex()
        self._byte_buffer = bytearray()
        self.bytes_scanned = 0          # 已扫描字节数（主线程可读）

    def set_running(self, val: bool):
        self.running = val

    def enqueue_data(self, data: bytes):
        with QMutexLocker(self._queue_mutex):
            self._queue.append(data)

    def run(self):
        while self.running:
            with QMutexLocker(self._queue_mutex):
                if self._queue:
                    data = self._queue.popleft()
                else:
                    data = None

            if data is not None:
                self.bytes_scanned += len(data)
                self._byte_buffer.extend(data)
                if len(self._byte_buffer) > 2 * 1024 * 1024:
                    del self._byte_buffer[:len(self._byte_buffer) - 1024 * 1024]

                if self.filter_mode == self.MODE_JSON_OBJECT:
                    batch = self._extract_json_objects()
                elif self.filter_mode == self.MODE_LINE_BY_LINE:
                    batch = self._extract_lines()
                elif self.filter_mode == self.MODE_BINARY:
                    batch = self._extract_binary()
                else:
                    batch = self._extract_regex()
                if batch:
                    self.items_ready.emit(batch)
                # 有数据时快速轮询（5ms），提高实时性
                self.msleep(5)
            else:
                # 空闲时慢速轮询（50ms），降低 CPU 占用
                self.msleep(50)

    def _try_parse(self, raw: str) -> tuple:
        """通用解析：尝试 json.loads，返回 (raw, obj, summary, parse_error)"""
        obj = None
        summary = ""
        try:
            obj = json.loads(raw)
            summary = _make_summary(obj)
        except json.JSONDecodeError:
            summary = raw[:SUMMARY_MAX_LEN] + "…"
            obj = None
        return (raw, obj, summary, not obj)

    def _extract_json_objects(self) -> list:
        """模式1：括号计数提取完整 JSON 对象（支持任意深度嵌套）"""
        batch = []
        text = self._byte_buffer.decode('utf-8', errors='replace')
        i = 0
        n = len(text)
        last_end = 0

        while i < n:
            ch = text[i]
            if ch not in ('{', '['):
                i += 1
                continue

            start = i
            depth = 0
            in_string = False
            escape = False
            open_char = ch
            close_char = '}' if ch == '{' else ']'

            i += 1
            while i < n:
                c = text[i]

                if escape:
                    escape = False
                    i += 1
                    continue

                if c == '\\':
                    escape = True
                    i += 1
                    continue

                if c == '"':
                    in_string = not in_string
                    i += 1
                    continue

                if in_string:
                    i += 1
                    continue

                if c == open_char:
                    depth += 1
                elif c == close_char:
                    if depth == 0:
                        raw = text[start:i + 1]
                        batch.append(self._try_parse(raw))
                        last_end = i + 1
                        i += 1
                        break
                    else:
                        depth -= 1

                i += 1
            else:
                break  # 未闭合，保留未完成部分

            i = max(i, start + 1)

        if last_end > 0:
            self._byte_buffer = bytearray(text[last_end:].encode('utf-8'))
        elif len(self._byte_buffer) > 256 * 1024:
            self._byte_buffer = self._byte_buffer[-65536:]

        return batch

    def _extract_lines(self) -> list:
        """模式2：按行分割，逐行尝试；JSON行正常解析，非JSON行也保留原文"""
        batch = []
        text = self._byte_buffer.decode('utf-8', errors='replace')
        lines = text.split('\n')
        incomplete = lines[-1]
        complete_lines = lines[:-1]

        for line in complete_lines:
            line = line.strip('\r')
            if not line.strip():
                continue
            # 尝试整行 JSON 解析
            batch.append(self._try_parse(line.strip()))

        self._byte_buffer = bytearray(incomplete.encode('utf-8'))
        return batch

    def _extract_regex(self) -> list:
        """模式3：自定义正则提取（每次匹配提取所在整行，便于查看完整数据包）"""
        if not self.custom_regex:
            return self._extract_json_objects()  # 回退
        batch = []
        text = self._byte_buffer.decode('utf-8', errors='replace')
        try:
            pattern = re.compile(self.custom_regex)
            seen_lines = set()  # 去重：同一行可能被多次匹配
            last_end = 0
            for m in pattern.finditer(text):
                # 提取匹配所在的整行（避免只看到一个孤立的匹配词）
                line_start = text.rfind('\n', 0, m.start()) + 1
                line_end = text.find('\n', m.end())
                if line_end == -1:
                    line_end = len(text)
                raw = text[line_start:line_end].strip('\r')
                if raw not in seen_lines:
                    seen_lines.add(raw)
                    batch.append(self._try_parse(raw))
                last_end = max(last_end, line_end)
            if last_end > 0:
                self._byte_buffer = bytearray(text[last_end:].encode('utf-8'))
            elif len(self._byte_buffer) > 256 * 1024:
                self._byte_buffer = self._byte_buffer[-65536:]
        except re.error:
            # 正则无效，回退到 JSON 对象提取
            return self._extract_json_objects()

        return batch

    def _extract_binary(self) -> list:
        """模式4：按二进制协议模板解包帧 → 生成 pseudo-JSON 条目"""
        proto = self.protocol_template
        if not proto or not proto.fields:
            return []

        sync = proto.frame_sync
        frame_len = proto.frame_len_calc
        if frame_len <= 0:
            return []

        batch = []
        # 限制单次调用最多提取 100 帧，防止积压时 UI 冻结
        max_frames = 100
        extracted = 0

        while extracted < max_frames:
            buf_len = len(self._byte_buffer)
            # ── 帧同步 ──
            if sync.mode == 'delimiter':
                try:
                    header = bytes.fromhex(sync.header.replace(' ', ''))
                except (ValueError, AttributeError):
                    break
                if not header:
                    break
                idx = self._byte_buffer.find(header)
                if idx < 0:
                    break
                if idx > 0:
                    del self._byte_buffer[:idx]
                if len(self._byte_buffer) < len(header) + frame_len:
                    break
                del self._byte_buffer[:len(header)]
                frame = bytes(self._byte_buffer[:frame_len])
                del self._byte_buffer[:frame_len]

            elif sync.mode == 'fixed_length':
                fl = sync.frame_len or frame_len
                if buf_len < fl:
                    break
                frame = bytes(self._byte_buffer[:fl])
                del self._byte_buffer[:fl]

            elif sync.mode == 'length_field':
                len_info = _DTYPE_MAP.get(sync.len_dtype)
                if not len_info:
                    break
                _, len_size, len_endian = len_info
                need = sync.len_offset + len_size
                if buf_len < need:
                    break
                raw_len_bytes = bytes(
                    self._byte_buffer[sync.len_offset : sync.len_offset + len_size]
                )
                raw_len = _struct.unpack(
                    len_endian + _DTYPE_MAP[sync.len_dtype][0], raw_len_bytes
                )[0]
                actual_len = raw_len + sync.len_adjust
                if actual_len <= 0 or actual_len > 65536:
                    # 异常长度，丢弃1字节重试
                    del self._byte_buffer[:1]
                    continue
                if buf_len < actual_len:
                    break
                frame = bytes(self._byte_buffer[:actual_len])
                del self._byte_buffer[:actual_len]
            else:
                break

            # ── 解包 ──
            obj = unpack_frame(frame, proto.fields)
            if obj:
                raw_text = json.dumps(obj, ensure_ascii=False)
                summary = _make_summary(obj)
                batch.append((raw_text, obj, summary, False))
            extracted += 1

        # 防止缓冲区无限增长
        if len(self._byte_buffer) > 2 * 1024 * 1024:
            del self._byte_buffer[:len(self._byte_buffer) - 1024 * 1024]

        return batch


# ──────────────────────────────────────────────
#  二进制协议编辑器对话框
# ──────────────────────────────────────────────
class ProtocolEditorDialog(QDialog):
    """二进制协议模板编辑器 — 表格式编辑字段 + HEX 预览"""

    def __init__(self, parent=None, template: ProtocolTemplate = None,
                 theme_callback=None, is_dark=True):
        super().__init__(parent)
        self._theme_callback = theme_callback
        self._is_dark = is_dark
        # 编辑副本
        self._proto = ProtocolTemplate()
        if template:
            self._proto.name = template.name
            self._proto.description = template.description
            self._proto.frame_sync = FrameSync.from_dict(template.frame_sync.to_dict())
            self._proto.fields = [
                ProtoField.from_dict(f.to_dict()) for f in template.fields
            ]

        self.setWindowTitle("二进制协议编辑器")
        self.resize(800, 600)
        self.setMinimumSize(700, 500)
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
        )
        self._init_ui()
        self._sync_ui_from_proto()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # ── 协议名称/描述 ──
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("名称:", font=QFont("Microsoft YaHei", 9)))
        self.edit_name = QLineEdit()
        self.edit_name.setFont(QFont("Consolas", 9))
        self.edit_name.setPlaceholderText("如: 传感器协议 v1")
        name_layout.addWidget(self.edit_name)
        layout.addLayout(name_layout)

        desc_layout = QHBoxLayout()
        desc_layout.addWidget(QLabel("描述:", font=QFont("Microsoft YaHei", 9)))
        self.edit_desc = QLineEdit()
        self.edit_desc.setFont(QFont("Consolas", 9))
        self.edit_desc.setPlaceholderText("可选")
        desc_layout.addWidget(self.edit_desc)
        layout.addLayout(desc_layout)

        # ── 帧同步设置 ──
        sync_group = QGroupBox("帧同步")
        sync_group.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        sync_layout = QHBoxLayout(sync_group)
        sync_layout.setSpacing(6)

        sync_layout.addWidget(QLabel("模式:", font=QFont("Microsoft YaHei", 9)))
        self.combo_sync_mode = QComboBox()
        self.combo_sync_mode.addItems(["delimiter (定界符)", "fixed_length (固定帧长)", "length_field (长度字段)"])
        self.combo_sync_mode.setFont(QFont("Microsoft YaHei", 9))
        self.combo_sync_mode.currentIndexChanged.connect(self._on_sync_mode_changed)
        sync_layout.addWidget(self.combo_sync_mode)

        # 定界符参数
        self.lbl_header = QLabel("Header(HEX):")
        self.lbl_header.setFont(QFont("Microsoft YaHei", 9))
        sync_layout.addWidget(self.lbl_header)
        self.edit_header = QLineEdit()
        self.edit_header.setFont(QFont("Consolas", 9))
        self.edit_header.setPlaceholderText("如 AA 55")
        self.edit_header.setMaximumWidth(100)
        sync_layout.addWidget(self.edit_header)

        # 固定帧长参数
        self.lbl_frame_len = QLabel("帧长(字节):")
        self.lbl_frame_len.setFont(QFont("Microsoft YaHei", 9))
        self.lbl_frame_len.hide()
        sync_layout.addWidget(self.lbl_frame_len)
        self.spin_frame_len = QSpinBox()
        self.spin_frame_len.setRange(1, 65535)
        self.spin_frame_len.setValue(8)
        self.spin_frame_len.setFont(QFont("Consolas", 9))
        self.spin_frame_len.hide()
        sync_layout.addWidget(self.spin_frame_len)

        # 长度字段参数
        self.lbl_len_offset = QLabel("偏移:")
        self.lbl_len_offset.setFont(QFont("Microsoft YaHei", 9))
        self.lbl_len_offset.hide()
        sync_layout.addWidget(self.lbl_len_offset)
        self.spin_len_offset = QSpinBox()
        self.spin_len_offset.setRange(0, 255)
        self.spin_len_offset.setFont(QFont("Consolas", 9))
        self.spin_len_offset.hide()
        sync_layout.addWidget(self.spin_len_offset)

        self.lbl_len_dtype = QLabel("类型:")
        self.lbl_len_dtype.setFont(QFont("Microsoft YaHei", 9))
        self.lbl_len_dtype.hide()
        sync_layout.addWidget(self.lbl_len_dtype)
        self.combo_len_dtype = QComboBox()
        self.combo_len_dtype.addItems(_DTYPE_CHOICES)
        self.combo_len_dtype.setCurrentText('uint8')
        self.combo_len_dtype.setFont(QFont("Consolas", 9))
        self.combo_len_dtype.hide()
        sync_layout.addWidget(self.combo_len_dtype)

        self.lbl_len_adjust = QLabel("修正值:")
        self.lbl_len_adjust.setFont(QFont("Microsoft YaHei", 9))
        self.lbl_len_adjust.hide()
        sync_layout.addWidget(self.lbl_len_adjust)
        self.spin_len_adjust = QSpinBox()
        self.spin_len_adjust.setRange(-100, 1000)
        self.spin_len_adjust.setValue(0)
        self.spin_len_adjust.setFont(QFont("Consolas", 9))
        self.spin_len_adjust.hide()
        sync_layout.addWidget(self.spin_len_adjust)

        sync_layout.addStretch()
        layout.addWidget(sync_group)

        # ── 字段定义表 ──
        fields_group = QGroupBox("字段定义")
        fields_group.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        fields_layout = QVBoxLayout(fields_group)

        self.fields_table = QTableWidget()
        self.fields_table.setColumnCount(7)
        self.fields_table.setHorizontalHeaderLabels(
            ["名称", "类型", "偏移", "缩放", "单位", "位偏移", "位宽"]
        )
        self.fields_table.verticalHeader().setVisible(False)
        self.fields_table.horizontalHeader().setStretchLastSection(True)
        self.fields_table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.fields_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # 设置合理列宽，避免类型下拉框被截断
        self.fields_table.setColumnWidth(0, 100)   # 名称
        self.fields_table.setColumnWidth(1, 110)   # 类型（uint16_le 等长文本）
        self.fields_table.setColumnWidth(2, 60)    # 偏移
        self.fields_table.setColumnWidth(3, 60)    # 缩放
        self.fields_table.setColumnWidth(4, 60)    # 单位
        fields_layout.addWidget(self.fields_table)

        btn_field_row = QHBoxLayout()
        btn_add = QPushButton("＋ 添加字段")
        btn_add.setFont(QFont("Microsoft YaHei", 9))
        btn_add.clicked.connect(self._add_field_row)
        btn_field_row.addWidget(btn_add)

        btn_remove = QPushButton("− 删除字段")
        btn_remove.setFont(QFont("Microsoft YaHei", 9))
        btn_remove.clicked.connect(self._remove_field_row)
        btn_field_row.addWidget(btn_remove)

        btn_field_row.addStretch()
        fields_layout.addLayout(btn_field_row)
        layout.addWidget(fields_group)

        # ── 预览区 ──
        preview_group = QGroupBox("HEX 预览")
        preview_group.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        preview_layout = QVBoxLayout(preview_group)

        preview_input_row = QHBoxLayout()
        preview_input_row.addWidget(QLabel("输入HEX:", font=QFont("Microsoft YaHei", 9)))
        self.edit_preview_hex = QLineEdit()
        self.edit_preview_hex.setFont(QFont("Consolas", 9))
        self.edit_preview_hex.setPlaceholderText("如 AA55 0064 3C  （可选，按当前协议解析预览）")
        self.edit_preview_hex.textChanged.connect(self._preview)
        preview_input_row.addWidget(self.edit_preview_hex)
        preview_layout.addLayout(preview_input_row)

        self.lbl_preview = QLabel("解析结果: —")
        self.lbl_preview.setFont(QFont("Consolas", 9))
        self.lbl_preview.setWordWrap(True)
        preview_layout.addWidget(self.lbl_preview)
        layout.addWidget(preview_group)

        # ── 操作按钮 ──
        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存模板")
        btn_save.setFont(QFont("Microsoft YaHei", 9))
        btn_save.clicked.connect(self._save_template)
        btn_row.addWidget(btn_save)

        btn_load = QPushButton("加载模板")
        btn_load.setFont(QFont("Microsoft YaHei", 9))
        btn_load.clicked.connect(self._load_template)
        btn_row.addWidget(btn_load)

        btn_row.addStretch()

        btn_ok = QPushButton("确定")
        btn_ok.setFont(QFont("Microsoft YaHei", 9))
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_ok)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFont(QFont("Microsoft YaHei", 9))
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        layout.addLayout(btn_row)

    def _on_sync_mode_changed(self, idx):
        """切换帧同步模式时显示/隐藏对应参数"""
        modes = [
            ['lbl_header', 'edit_header'],
            ['lbl_frame_len', 'spin_frame_len'],
            ['lbl_len_offset', 'spin_len_offset', 'lbl_len_dtype',
             'combo_len_dtype', 'lbl_len_adjust', 'spin_len_adjust'],
        ]
        all_widgets = set()
        for group in modes:
            for wn in group:
                all_widgets.add(wn)
        for wn in all_widgets:
            w = getattr(self, wn, None)
            if w:
                w.hide()

        if 0 <= idx < len(modes):
            for wn in modes[idx]:
                w = getattr(self, wn, None)
                if w:
                    w.show()

    def _sync_ui_from_proto(self):
        """从 self._proto 刷新 UI"""
        self.edit_name.setText(self._proto.name)
        self.edit_desc.setText(self._proto.description)

        sync = self._proto.frame_sync
        mode_map = {'delimiter': 0, 'fixed_length': 1, 'length_field': 2}
        self.combo_sync_mode.setCurrentIndex(mode_map.get(sync.mode, 0))
        self.edit_header.setText(sync.header)
        self.spin_frame_len.setValue(sync.frame_len or 8)
        self.spin_len_offset.setValue(sync.len_offset)
        self.combo_len_dtype.setCurrentText(sync.len_dtype)
        self.spin_len_adjust.setValue(sync.len_adjust)
        self._on_sync_mode_changed(self.combo_sync_mode.currentIndex())

        # 字段表
        self._rebuild_fields_table()

    def _rebuild_fields_table(self):
        """从 self._proto.fields 重建表格"""
        self.fields_table.setRowCount(0)
        for f in self._proto.fields:
            self._add_field_row(prefill=f)

    def _add_field_row(self, prefill=None):
        """添加一行字段编辑"""
        r = self.fields_table.rowCount()
        self.fields_table.insertRow(r)

        # 名称
        name = prefill.name if prefill else ""
        name_item = QTableWidgetItem(name)
        self.fields_table.setItem(r, 0, name_item)

        # 类型（ComboBox）
        dtype = prefill.dtype if prefill else "uint8"
        combo_type = QComboBox()
        combo_type.addItems(_DTYPE_CHOICES)
        combo_type.setCurrentText(dtype)
        combo_type.setFont(QFont("Consolas", 9))
        self.fields_table.setCellWidget(r, 1, combo_type)

        # 偏移
        offset = prefill.offset if prefill else 0
        offset_item = QTableWidgetItem(str(offset))
        self.fields_table.setItem(r, 2, offset_item)

        # 缩放
        scale = prefill.scale if prefill else 1.0
        scale_item = QTableWidgetItem(str(scale))
        self.fields_table.setItem(r, 3, scale_item)

        # 单位
        unit = prefill.unit if prefill else ""
        unit_item = QTableWidgetItem(unit)
        self.fields_table.setItem(r, 4, unit_item)

        # 位偏移
        bit_off = prefill.bit_offset if prefill else 0
        bit_off_item = QTableWidgetItem(str(bit_off))
        self.fields_table.setItem(r, 5, bit_off_item)

        # 位宽
        bit_w = prefill.bit_width if prefill else 0
        bit_w_item = QTableWidgetItem(str(bit_w))
        self.fields_table.setItem(r, 6, bit_w_item)

    def _remove_field_row(self):
        rows = set(idx.row() for idx in self.fields_table.selectionModel().selectedRows())
        if not rows:
            return
        for r in sorted(rows, reverse=True):
            self.fields_table.removeRow(r)

    def _collect_fields(self) -> list[ProtoField]:
        """从表格收集字段定义"""
        fields = []
        for r in range(self.fields_table.rowCount()):
            name = self.fields_table.item(r, 0).text().strip() if self.fields_table.item(r, 0) else ""
            if not name:
                continue
            combo = self.fields_table.cellWidget(r, 1)
            dtype = combo.currentText() if combo else "uint8"
            try:
                offset = int(self.fields_table.item(r, 2).text()) if self.fields_table.item(r, 2) else 0
            except (ValueError, AttributeError):
                offset = 0
            try:
                scale = float(self.fields_table.item(r, 3).text()) if self.fields_table.item(r, 3) else 1.0
            except (ValueError, AttributeError):
                scale = 1.0
            unit = self.fields_table.item(r, 4).text().strip() if self.fields_table.item(r, 4) else ""
            try:
                bit_off = int(self.fields_table.item(r, 5).text()) if self.fields_table.item(r, 5) else 0
            except (ValueError, AttributeError):
                bit_off = 0
            try:
                bit_w = int(self.fields_table.item(r, 6).text()) if self.fields_table.item(r, 6) else 0
            except (ValueError, AttributeError):
                bit_w = 0
            fields.append(ProtoField(name, dtype, offset, scale, unit, bit_off, bit_w))
        return fields

    def _collect_proto(self) -> ProtocolTemplate:
        """从 UI 收集完整协议模板"""
        proto = ProtocolTemplate()
        proto.name = self.edit_name.text().strip()
        proto.description = self.edit_desc.text().strip()

        mode_idx = self.combo_sync_mode.currentIndex()
        mode_map = {0: 'delimiter', 1: 'fixed_length', 2: 'length_field'}
        sync = FrameSync(mode=mode_map.get(mode_idx, 'delimiter'))
        sync.header = self.edit_header.text().strip()
        sync.frame_len = self.spin_frame_len.value()
        sync.len_offset = self.spin_len_offset.value()
        sync.len_dtype = self.combo_len_dtype.currentText()
        sync.len_adjust = self.spin_len_adjust.value()
        proto.frame_sync = sync

        proto.fields = self._collect_fields()
        return proto

    def _preview(self):
        """用输入 HEX 预览解析结果"""
        hex_str = self.edit_preview_hex.text().strip()
        if not hex_str:
            self.lbl_preview.setText("解析结果: —")
            return
        try:
            frame = bytes.fromhex(hex_str.replace(' ', ''))
        except ValueError:
            self.lbl_preview.setText("解析结果: HEX 格式错误")
            return

        proto = self._collect_proto()
        obj = unpack_frame(frame, proto.fields)
        if obj:
            preview = {}
            for k, v in obj.items():
                if k.startswith('_'):
                    continue
                preview[k] = v
            self.lbl_preview.setText(f"解析结果: {json.dumps(preview, ensure_ascii=False)}")
        else:
            self.lbl_preview.setText("解析结果: 无字段定义")

    def _save_template(self):
        proto = self._collect_proto()
        path, _ = QFileDialog.getSaveFileName(
            self, "保存协议模板", f"{proto.name or 'protocol'}.json",
            "JSON (*.json)"
        )
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(proto.to_dict(), f, indent=2, ensure_ascii=False)

    def _load_template(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载协议模板", "", "JSON (*.json)"
        )
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                self._proto = ProtocolTemplate.from_dict(d)
                self._sync_ui_from_proto()
            except Exception as e:
                QMessageBox.warning(self, "加载失败", f"无法加载协议模板:\n{e}")

    def get_template(self) -> ProtocolTemplate:
        """返回编辑后的协议模板"""
        return self._collect_proto()


# ──────────────────────────────────────────────
#  主对话框
# ──────────────────────────────────────────────
class JsonViewerDialog(QDialog):
    """数据分析面板 — 主对话框"""

    def __init__(self, parent=None, theme_callback=None, arrow_paths=None, is_dark=True):
        super().__init__(parent)
        self._theme_callback = theme_callback      # 父窗口的 _apply_dialog_theme 方法
        self._arrow_paths = arrow_paths or {}      # 箭头图标路径
        self._is_dark = is_dark                    # 初始主题状态
        self._capture_running = False
        self._capture_count = 0
        self._capture_rate = 0
        self._rate_window = deque(maxlen=20)       # 速率滑动窗口（每秒）
        self._last_data_ts = 0                     # 最后收到原始数据的时间戳
        self._seq_counter = 0
        self._protocol_template: ProtocolTemplate | None = None  # 二进制协议模板

        self.setWindowTitle("数据分析面板")
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)
        # 作为独立工具窗口：允许最小化/最大化，不强制置顶
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )

        self._init_ui()
        self._restore_layout()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # ── 顶部控制栏 ──
        ctrl_widget = QWidget()
        ctrl_layout = QHBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(4, 2, 4, 2)
        ctrl_layout.setSpacing(6)

        # ─ 过滤模式 ─
        flt_label = QLabel("过滤:")
        flt_label.setFont(QFont("Microsoft YaHei", 9))
        ctrl_layout.addWidget(flt_label)
        self.combo_filter_mode = QComboBox()
        self.combo_filter_mode.addItems(["所有行尝试解析", "提取 JSON 对象", "自定义正则", "二进制协议解析"])
        self.combo_filter_mode.setFont(QFont("Microsoft YaHei", 9))
        self.combo_filter_mode.setToolTip("选择数据解析策略")
        self.combo_filter_mode.currentIndexChanged.connect(self._on_filter_mode_changed)
        ctrl_layout.addWidget(self.combo_filter_mode)

        # 协议状态（仅二进制模式可见）
        self.lbl_protocol = QLabel("协议: 未选择")
        self.lbl_protocol.setFont(QFont("Microsoft YaHei", 9))
        self.lbl_protocol.setVisible(False)
        ctrl_layout.addWidget(self.lbl_protocol)

        self.btn_edit_protocol = QPushButton("编辑协议")
        self.btn_edit_protocol.setFont(QFont("Microsoft YaHei", 9))
        self.btn_edit_protocol.setToolTip("编辑二进制协议模板（字段定义、帧同步方式）")
        self.btn_edit_protocol.setVisible(False)
        self.btn_edit_protocol.clicked.connect(self._open_protocol_editor)
        ctrl_layout.addWidget(self.btn_edit_protocol)

        # 正则输入框（自定义正则模式可见）
        self.edit_regex = QLineEdit()
        self.edit_regex.setPlaceholderText("正则表达式，如: value=(\\d+\\.\\d+)")
        self.edit_regex.setFont(QFont("Consolas", 9))
        self.edit_regex.setMaximumWidth(260)
        self.edit_regex.setToolTip(
            "输入正则表达式从数据流中提取内容\n"
            "匹配到的文本会尝试 JSON 解析后显示"
        )
        self.edit_regex.setVisible(False)
        self.edit_regex.textChanged.connect(self._on_regex_changed)
        ctrl_layout.addWidget(self.edit_regex)

        # 搜索
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("搜索列表…")
        self.edit_search.setFont(QFont("Microsoft YaHei", 9))
        self.edit_search.setMaximumWidth(160)
        self.edit_search.setToolTip("输入关键字实时过滤列表 (Ctrl+F)")
        ctrl_layout.addWidget(self.edit_search)

        ctrl_layout.addStretch()

        # ─ 操作按钮 ─
        self.btn_start = QPushButton("开始监听")
        self.btn_start.setFont(QFont("Microsoft YaHei", 9))
        self.btn_start.setToolTip("开始从数据源捕获 (Ctrl+Enter)")
        self.btn_start.clicked.connect(self._start_capture)
        ctrl_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("停止捕获")
        self.btn_stop.setFont(QFont("Microsoft YaHei", 9))
        self.btn_stop.setEnabled(False)
        self.btn_stop.setToolTip("停止捕获 (Ctrl+Enter)")
        self.btn_stop.clicked.connect(self._stop_capture)
        ctrl_layout.addWidget(self.btn_stop)

        self.btn_clear = QPushButton("清空全部")
        self.btn_clear.setFont(QFont("Microsoft YaHei", 9))
        self.btn_clear.setToolTip("清空所有捕获数据和图表")
        self.btn_clear.clicked.connect(self._clear_all)
        ctrl_layout.addWidget(self.btn_clear)

        # ─ 帮助 ─
        self.btn_help = QPushButton("?")
        self.btn_help.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        self.btn_help.setFixedWidth(28)
        self.btn_help.setToolTip("查看使用说明")
        self.btn_help.clicked.connect(self._show_help)
        ctrl_layout.addWidget(self.btn_help)

        main_layout.addWidget(ctrl_widget)

        # ──── 主分割器（左右） ────
        self.splitter_h = QSplitter(Qt.Horizontal)

        # 左侧面板：状态指示 + 捕获列表（状态仅在左侧，不占用右侧空间）
        left_panel = QWidget()
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_layout.setSpacing(2)

        self.lbl_status = QLabel("状态: 就绪 | 捕获 0 条 | 速率 0/s")
        self.lbl_status.setFont(QFont("Consolas", 10))
        self.lbl_status.setTextFormat(Qt.PlainText)
        self.lbl_status.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(128,128,128,0.12);"
            "  border-radius: 4px;"
            "  padding: 3px 10px;"
            "}"
        )
        left_panel_layout.addWidget(self.lbl_status)

        self.capture_list = CaptureListWidget(is_dark=self._is_dark)
        left_panel_layout.addWidget(self.capture_list, stretch=1)

        self.splitter_h.addWidget(left_panel)

        # 右侧：垂直分割器（详情 + 图表 + 数据表格）
        self.splitter_v = QSplitter(Qt.Vertical)

        self.chart_tracker = ChartTrackerWidget(is_dark=self._is_dark)
        self.detail_viewer = DetailViewerWidget(chart_tracker=self.chart_tracker)
        self.detail_viewer._export_all_callback = self._export_all_jsonl

        # Feature 4: 数据表格（图表下方，可折叠）
        self.data_table = DataTableWidget(chart_tracker=self.chart_tracker)
        self.data_table.setVisible(False)  # 默认隐藏

        self.splitter_v.addWidget(self.detail_viewer)   # 上半：详情查看器

        # 图表+数据表格 嵌套分割器
        self.chart_data_splitter = QSplitter(Qt.Vertical)
        self.chart_data_splitter.addWidget(self.chart_tracker)
        self.chart_data_splitter.addWidget(self.data_table)
        self.chart_data_splitter.setSizes([350, 0])  # 初始隐藏数据表格

        self.splitter_v.addWidget(self.chart_data_splitter)    # 下半：图表+数据表格

        self.splitter_h.addWidget(self.splitter_v)

        # 初始比例 左:右 = 35:65
        self.splitter_h.setSizes([420, 780])
        self.splitter_v.setSizes([250, 500])

        main_layout.addWidget(self.splitter_h, stretch=1)

        # Feature 2: 告警状态变化连接
        self.chart_tracker.alert_state_changed.connect(self._on_alert_changed)

        # Feature 4: 数据表格切换（从图表区按钮触发）
        self.chart_tracker.toggle_data_table_requested.connect(self._toggle_data_table)

        # ──── 内部信号连接 ────
        # 搜索防抖
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search)
        self.edit_search.textChanged.connect(lambda: self._search_timer.start(SEARCH_DEBOUNCE_MS))

        # 列表选中 → 详情查看
        self.capture_list.item_selected.connect(self._on_item_selected)

        # ──── 后台捕获线程 ────
        self._capture_thread = JsonCaptureThread()
        self._capture_thread.items_ready.connect(self._on_items_ready)

        # 批量出队定时器
        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(BATCH_UPDATE_MS)
        self._batch_timer.timeout.connect(self._flush_pending)
        self._pending_batch: list = []

        # 速率定时器（1s）
        self._rate_timer = QTimer(self)
        self._rate_timer.setInterval(1000)
        self._rate_timer.timeout.connect(self._update_rate)
        self._rate_timer.start()

        # 慢刷新定时器（500ms — 统计面板 + 数据表格，不需要 30ms 高频）
        self._slow_timer = QTimer(self)
        self._slow_timer.setInterval(500)
        self._slow_timer.timeout.connect(self._slow_refresh)
        self._slow_timer.start()

    def set_theme(self, is_dark: bool):
        """响应外部主题切换，更新所有子组件配色"""
        self._is_dark = is_dark
        self.capture_list.set_theme(is_dark)
        self.chart_tracker.set_theme(is_dark)
        self.detail_viewer.set_theme(is_dark)
        if hasattr(self, 'data_table') and self.data_table:
            self.data_table.set_theme(is_dark)

    # --- 数据接入 ---
    def _set_status_style(self, color=None, bold=False):
        """设置状态标签样式（保留基础背景/圆角/内边距）"""
        extra = ""
        if color:
            extra += f"color: {color};"
        if bold:
            extra += "font-weight: bold;"
        self.lbl_status.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(128,128,128,0.12);"
            "  border-radius: 4px;"
            "  padding: 3px 10px;" + extra +
            "}"
        )

    def feed_raw_data(self, data: bytes):
        """外部调用：喂入原始 bytes 数据"""
        if not hasattr(self, '_dbg_rx_count'):
            self._dbg_rx_count = 0
            self._dbg_rx_bytes = 0
        self._dbg_rx_count += 1
        self._dbg_rx_bytes += len(data)
        self._last_data_ts = time.time()  # 记录最后收到数据的时间戳

        if not self._capture_running:
            self._set_status_style(color="#E06C75", bold=True)
            self.lbl_status.setText(f"状态: 未启动 | 已收到 {self._dbg_rx_bytes}B | 请点击「开始监听」")
            return
        self._capture_thread.enqueue_data(data)

    def _on_items_ready(self, batch: list):
        """后台线程解析完成，加入待刷新队列"""
        self._pending_batch.extend(batch)

    def _flush_pending(self):
        """批量追加到列表模型"""
        if not self._pending_batch:
            return
        items = []
        for raw, obj, summary, parse_err in self._pending_batch:
            self._seq_counter += 1
            self._capture_count += 1
            now_ts = datetime.datetime.now()
            ts = now_ts.strftime("%H:%M:%S.") + f"{now_ts.microsecond // 1000:03d}"
            items.append({
                'seq': self._seq_counter,
                'timestamp': ts,
                'summary': summary,
                'length': len(raw),
                'raw': raw,
                'obj': obj,
                'parse_error': parse_err,
            })

        self._pending_batch.clear()
        self.capture_list.append_items(items)

        # 速率统计
        self._rate_window.append(time.time())

        # 图表数据喂养
        for it in items:
            if it['obj'] is not None:
                self.chart_tracker.feed_data(it['obj'])
            else:
                # 非 JSON 行也尝试提取 name=value / name:value 模式给图表
                pseudo = _extract_kv_pairs(it.get('raw', ''))
                if pseudo:
                    self.chart_tracker.feed_data(pseudo)

        # 统计面板 + 数据表格由 _slow_refresh (500ms) 统一刷新

    def _show_help(self):
        """显示使用说明"""
        QMessageBox.information(self, "数据分析面板 — 使用说明",
            "<b>━━ 基本操作 ━━</b><br>"
            "1. 主窗口打开串口连接后，点击 <b>「▶ 开始监听」</b> 开始捕获<br>"
            "2. 根据数据格式选择过滤模式：<br>"
            "&nbsp;&nbsp;&nbsp;<b>提取 JSON 对象</b> — {'{…}'} / {'[…]'} 格式<br>"
            "&nbsp;&nbsp;&nbsp;<b>所有行尝试解析</b> — 逐行捕获（含非JSON文本）<br>"
            "&nbsp;&nbsp;&nbsp;<b>自定义正则</b> — 正则表达式匹配<br>"
            "&nbsp;&nbsp;&nbsp;<b>二进制协议解析</b> — struct 模板解包<br>"
            "3. 点击左侧列表项 → 右侧显示详情<br>"
            "4. 搜索框实时过滤列表内容<br><br>"
            "<b>━━ 图表跟踪 ━━</b><br>"
            "• 从树形视图 <b>拖拽字段到图表区</b> 即可添加折线<br>"
            "• 右键跟踪标签可设置别名、阈值、颜色<br>"
            "• 超出阈值自动告警（状态栏变红 + 图表红框）<br>"
            "• 鼠标悬停图表显示十字线和数值<br><br>"
            "<b>━━ 计算字段 ━━</b><br>"
            "• 点击 <b>「fx 计算字段」</b> 创建派生字段<br>"
            "• 语法：field_name 或 data.volt（点号路径）<br>"
            "• 支持 + - * / ** % ( ) 四则运算<br><br>"
            "<b>━━ 散点图 ━━</b><br>"
            "• 点击 <b>「切换散点图」</b> 进入散点图模式<br>"
            "• X=采样序号 → 所有字段各色散点<br>"
            "• X=字段A → 字段A vs 字段B 相关性 + 回归线(R²)<br><br>"
            "<b>━━ 统计面板 ━━</b><br>"
            "• 详情视图切换到「统计」查看实时 min/max/mean/std<br><br>"
            "<b>━━ 数据表格 ━━</b><br>"
            "• 图表下方 <b>「⊞ 数据表」</b>展示最近 N 行原始数值<br><br>"
            "<b>━━ 快捷键 ━━</b><br>"
            "Ctrl+Enter — 开始/停止监听 &nbsp;|&nbsp; Escape — 关闭面板<br>"
            "Ctrl+F — 聚焦搜索框 &nbsp;|&nbsp; Delete — 删除选中项<br>"
            "Space — 暂停/继续图表"
        )

    def _toggle_data_table(self):
        """切换数据表格显示/隐藏"""
        btn = self.chart_tracker.btn_toggle_table
        if self.data_table.isVisible():
            self.data_table.setVisible(False)
            btn.setText("⊞ 数据表")
            self.chart_data_splitter.setSizes([350, 0])
        else:
            self.data_table.setVisible(True)
            self.data_table.refresh()
            btn.setText("⊟ 数据表")
            self.chart_data_splitter.setSizes([280, 150])

    def _on_alert_changed(self, active: bool):
        """Feature 2: 告警状态变化时更新状态栏"""
        if active:
            active_count, total_count = self.chart_tracker.get_alert_count()
            self._set_status_style(color="#E06C75", bold=True)
            self.lbl_status.setText(
                f"状态: ⚠ 告警中 | 捕获 {self._capture_count} 条 | "
                f"活跃 {active_count} 告警 (共 {total_count} 次)"
            )
        else:
            # 恢复默认样式并触发一次速率刷新以恢复正常文本
            self._set_status_style()
            if self._capture_running:
                self._update_rate()

    def _export_all_jsonl(self):
        """导出全部已捕获的 JSON 为 .jsonl 文件"""
        path, _ = QFileDialog.getSaveFileName(self, "导出全部 JSONL", "all_data.jsonl", "JSONL (*.jsonl)")
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                for item in self.capture_list.model._items:
                    f.write(item.get('raw', '') + '\n')

    def _update_rate(self):
        """每秒更新速率显示 + 诊断提示（保留告警样式不覆盖）"""
        # 未启动时不更新（保持 feed_raw_data 设置的提示）
        if not self._capture_running:
            return

        now = time.time()
        while self._rate_window and now - self._rate_window[0] > 10:
            self._rate_window.popleft()
        rate = len(self._rate_window) / 10.0 if self._rate_window else 0

        # 如果告警活跃，保留告警样式和文本（由 _on_alert_changed 管理）
        if self.chart_tracker._alert_active_global:
            return

        # 诊断提示：仅在「提取 JSON 对象」模式下，有数据但无 JSON 对象时提醒
        hint = ""
        data_active = (now - self._last_data_ts < 3)  # 3 秒内有数据流入
        if (self._capture_count == 0 and data_active
                and self._capture_thread.filter_mode == JsonCaptureThread.MODE_JSON_OBJECT):
            hint = "  [!] 未检测到JSON对象"
            self._set_status_style(color="#E06C75", bold=True)
        else:
            self._set_status_style()

        self.lbl_status.setText(
            f"状态: 正在捕获 | 捕获 {self._capture_count} 条 | 速率 {rate:.1f}/s{hint}"
        )

    def _slow_refresh(self):
        """500ms 慢刷新：统计面板 + 数据表格（不需要 30ms 高频）"""
        if hasattr(self, 'detail_viewer') and hasattr(self.detail_viewer, 'stats_panel'):
            self.detail_viewer.stats_panel.refresh()
        if hasattr(self, 'data_table') and self.data_table.isVisible():
            self.data_table.refresh()

    def _on_regex_changed(self, text: str):
        """正则表达式文本变更 → 实时同步到运行中的捕获线程"""
        if hasattr(self, '_capture_thread') and self._capture_thread.isRunning():
            self._capture_thread.custom_regex = text.strip()
            # 切换正则后清空缓冲区重新匹配
            self._capture_thread._byte_buffer = bytearray()

    def _on_filter_mode_changed(self, idx: int):
        """捕获中切换过滤模式时实时更新后台线程"""
        is_binary = (idx == 3)      # MODE_BINARY
        is_regex = (idx == 2)       # MODE_REGEX

        self.lbl_protocol.setVisible(is_binary)
        self.btn_edit_protocol.setVisible(is_binary)
        self.edit_regex.setVisible(is_regex)

        if is_binary:
            self._update_protocol_label()

        if hasattr(self, '_capture_thread') and self._capture_thread.isRunning():
            self._capture_thread.filter_mode = idx
            self._capture_thread.custom_regex = self.edit_regex.text().strip() if is_regex else ""
            self._capture_thread.protocol_template = (
                self._protocol_template if is_binary else None
            )
            # 清空字节缓冲区，因为不同模式的解析逻辑不同
            self._capture_thread._byte_buffer = bytearray()

    def _update_protocol_label(self):
        """更新协议状态标签"""
        t = _VIEWER_TOKENS['dark' if self._is_dark else 'light']
        if self._protocol_template and self._protocol_template.fields:
            count = len(self._protocol_template.fields)
            self.lbl_protocol.setText(
                f"协议: {self._protocol_template.name} ({count}字段)"
            )
            self.lbl_protocol.setStyleSheet(f"color: {t['success']};")
        else:
            self.lbl_protocol.setText("协议: 未选择 — 请点击「编辑协议」")
            self.lbl_protocol.setStyleSheet(f"color: {t['danger']};")

    def _open_protocol_editor(self):
        """打开二进制协议编辑器"""
        dlg = ProtocolEditorDialog(
            self, template=self._protocol_template,
            theme_callback=self._theme_callback,
            is_dark=(self.current_theme if hasattr(self, 'current_theme') else True),
        )
        # 应用主题
        if self._theme_callback:
            self._theme_callback(dlg)

        if dlg.exec_() == QDialog.Accepted:
            self._protocol_template = dlg.get_template()
            self._update_protocol_label()
            # 同步到后台线程
            if hasattr(self, '_capture_thread') and self._capture_thread.isRunning():
                self._capture_thread.protocol_template = self._protocol_template
                self._capture_thread._byte_buffer = bytearray()

    # --- 捕获控制 ---
    def _start_capture(self):
        # QThread 只能 start 一次，每次开始捕获都创建新线程
        old_thread = self._capture_thread
        if old_thread.isRunning():
            old_thread.set_running(False)
            old_thread.wait(2000)

        try:
            old_thread.items_ready.disconnect(self._on_items_ready)
        except (TypeError, RuntimeError):
            pass

        self._capture_thread = JsonCaptureThread()
        self._capture_thread.items_ready.connect(self._on_items_ready)
        self._capture_thread.filter_mode = self.combo_filter_mode.currentIndex()
        if self._capture_thread.filter_mode == JsonCaptureThread.MODE_BINARY:
            self._capture_thread.protocol_template = self._protocol_template
        elif self._capture_thread.filter_mode == JsonCaptureThread.MODE_REGEX:
            self._capture_thread.custom_regex = self.edit_regex.text().strip()
        self._capture_thread.set_running(True)
        self._capture_thread.start()

        self._batch_timer.start()
        self._capture_running = True

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.edit_search.setEnabled(True)

    def _stop_capture(self):
        self._capture_running = False
        self._batch_timer.stop()
        self._flush_pending()

        # 通知线程停止并等待结束
        if self._capture_thread.isRunning():
            self._capture_thread.set_running(False)
            self._capture_thread.wait(2000)

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_status_style()
        self.lbl_status.setText("状态: 就绪 | 捕获 0 条 | 速率 0/s")

    def _clear_all(self):
        reply = QMessageBox.question(
            self, "确认清空",
            "确定要清空所有捕获数据和图表吗？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._capture_count = 0
        self._seq_counter = 0
        self._rate_window.clear()
        self.capture_list.clear()
        self.chart_tracker.clear_all_data()
        # 清空告警
        self.chart_tracker._alerts.clear()
        self.chart_tracker._alert_active_global = False
        self.chart_tracker.alert_state_changed.emit(False)
        # 清空数据表格
        if hasattr(self, 'data_table') and self.data_table:
            self.data_table.clear_all()
        self._set_status_style()
        self.lbl_status.setText("状态: 就绪 | 捕获 0 条 | 速率 0/s")

    # --- 搜索 ---
    def _apply_search(self):
        text = self.edit_search.text()
        self.capture_list.set_filter(text)

    # --- 列表选中 → 详情 ---
    def _on_item_selected(self, row: int):
        item = self.capture_list.model.get_item(row)
        if item:
            raw = item.get('raw', '')
            obj = item.get('obj', None)
            seq = item.get('seq', 0)
            self.detail_viewer.set_data(raw, obj, seq)

    # --- 布局持久化 ---
    def _save_layout(self):
        try:
            import configparser
            ini = configparser.ConfigParser()
            ini['layout'] = {
                'splitter_h': self.splitter_h.saveState().toHex().data().decode('utf-8'),
                'splitter_v': self.splitter_v.saveState().toHex().data().decode('utf-8'),
            }
            # 保存 chart_data_splitter 状态
            if hasattr(self, 'chart_data_splitter'):
                ini['layout']['chart_data_splitter'] = (
                    self.chart_data_splitter.saveState().toHex().data().decode('utf-8')
                )
            # 保存跟踪字段列表（含计算字段信息）
            tracked = self.chart_tracker._tracked
            ini['tracks'] = {
                'paths': json.dumps(list(tracked.keys())),
                'aliases': json.dumps({p: e['chip'].alias for p, e in tracked.items()}),
                'is_computed': json.dumps({p: e.get('is_computed', False) for p, e in tracked.items()}),
                'expressions': json.dumps({p: e.get('expression', '') for p, e in tracked.items()}),
            }
            with open('data_viewer.ini', 'w', encoding='utf-8') as f:
                ini.write(f)
        except Exception:
            pass

    def _restore_layout(self):
        try:
            import configparser
            ini = configparser.ConfigParser()
            if not ini.read('data_viewer.ini'):
                return
            if 'layout' in ini:
                h = QByteArray.fromHex(ini['layout']['splitter_h'].encode('utf-8'))
                v = QByteArray.fromHex(ini['layout']['splitter_v'].encode('utf-8'))
                self.splitter_h.restoreState(h)
                self.splitter_v.restoreState(v)
                # 恢复 chart_data_splitter
                if hasattr(self, 'chart_data_splitter') and 'chart_data_splitter' in ini['layout']:
                    cd = QByteArray.fromHex(ini['layout']['chart_data_splitter'].encode('utf-8'))
                    self.chart_data_splitter.restoreState(cd)
            # 恢复跟踪字段（含计算字段）—— 批量模式，避免每次 add 都重建下拉框
            if 'tracks' in ini:
                paths = json.loads(ini['tracks'].get('paths', '[]'))
                aliases = json.loads(ini['tracks'].get('aliases', '{}'))
                is_computed_map = json.loads(ini['tracks'].get('is_computed', '{}'))
                expr_map = json.loads(ini['tracks'].get('expressions', '{}'))
                try:
                    self.chart_tracker._batch_restoring = True
                    for path in paths:
                        alias = aliases.get(path, '')
                        is_comp = is_computed_map.get(path, False)
                        expr = expr_map.get(path, '')
                        self.chart_tracker.add_field(
                            path, alias=alias, is_computed=is_comp, expression=expr
                        )
                finally:
                    self.chart_tracker._batch_restoring = False
                    self.chart_tracker._update_scatter_combos()
        except Exception:
            pass

    # --- 键盘快捷键 ---
    def keyPressEvent(self, event):
        """全局键盘快捷键"""
        # Ctrl+Enter → 开始/停止监听
        if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            if self._capture_running:
                self._stop_capture()
            else:
                self._start_capture()
            return
        # Escape → 关闭面板
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    # --- 生命周期 ---
    def closeEvent(self, event):
        self._save_layout()
        self._stop_capture()
        event.accept()

    def reject(self):
        self._save_layout()
        self._stop_capture()
        super().reject()


# ──────────────────────────────────────────────
#  工具函数
# ──────────────────────────────────────────────
# 非JSON文本中的 name=value / name:value 提取正则
_KV_PATTERN = re.compile(r'(\w+)[:=]\s*(-?\d+\.?\d*)')

def _extract_kv_pairs(text: str) -> dict:
    """从任意文本中提取 key=value / key:value 数字对，返回伪 dict 供图表跟踪"""
    pairs = {}
    for m in _KV_PATTERN.finditer(text):
        key, val = m.group(1), m.group(2)
        try:
            pairs[key] = float(val) if '.' in val else int(val)
        except ValueError:
            pass
    return pairs if pairs else None


def _make_summary(obj) -> str:
    """从 JSON 对象生成简短摘要"""
    if isinstance(obj, dict):
        # 取前几个键值
        keys = list(obj.keys())
        if not keys:
            return "{}"
        parts = []
        for k in keys[:3]:
            v = obj[k]
            if isinstance(v, (int, float)):
                parts.append(f"{k}={v}")
            elif isinstance(v, str):
                parts.append(f"{k}={v[:15]}")
            elif isinstance(v, bool):
                parts.append(f"{k}={v}")
            else:
                parts.append(f"{k}=...")
        suffix = "…" if len(keys) > 3 else ""
        return ", ".join(parts) + suffix
    elif isinstance(obj, list):
        return f"[{len(obj)} items]"
    else:
        return str(obj)[:SUMMARY_MAX_LEN]


def _flatten_dict(d: dict, prefix: str = "") -> dict:
    """将嵌套字典扁平化为点号连接的键"""
    result = {}
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten_dict(v, full_key))
        elif isinstance(v, list):
            if v and isinstance(v[0], dict):
                # 不递归展平数组中的对象
                result[full_key] = json.dumps(v, ensure_ascii=False)[:200]
            else:
                result[full_key] = str(v)
        else:
            result[full_key] = v
    return result


def _extract_json_path(obj, path: str):
    """从 JSON 对象按路径提取值（简化版 jsonpath）"""
    # 支持 $.foo.bar 或 .foo.bar 或 foo.bar 或 foo[0].bar
    path = path.strip()
    if path.startswith('$.'):
        path = path[2:]
    elif path.startswith('$') or path.startswith('\\$'):
        path = path[path.index('$') + 1:]  # strip \$ or $
    elif path.startswith('.'):
        path = path[1:]

    # 拆分路径
    tokens = re.split(r'\.(?![^\[]*\])', path)
    current = obj
    for token in tokens:
        if current is None:
            return None
        # 处理数组索引 token[idx]
        m = re.match(r'^(\w+)\[(-?\d+)\]$', token)
        if m:
            key, idx = m.group(1), int(m.group(2))
            current = current.get(key) if isinstance(current, dict) else None
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        elif token.endswith(']') and '[' in token:
            # 纯索引 [0]
            m2 = re.match(r'^\[(-?\d+)\]$', token)
            if m2:
                idx = int(m2.group(1))
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
                continue
            return None
        else:
            if isinstance(current, dict):
                current = current.get(token)
            else:
                return None
    return current


def _dict_diff(a, b, path="$"):
    """递归比较两个 dict 的差异，返回差异字符串列表"""
    diffs = []
    all_keys = set()
    if isinstance(a, dict) and isinstance(b, dict):
        all_keys = set(a.keys()) | set(b.keys())
        for k in sorted(all_keys):
            child_path = f"{path}.{k}"
            if k not in a:
                diffs.append(f"+ {child_path}: {json.dumps(b[k], ensure_ascii=False)[:100]}")
            elif k not in b:
                diffs.append(f"- {child_path}: {json.dumps(a[k], ensure_ascii=False)[:100]}")
            else:
                if a[k] != b[k]:
                    if isinstance(a[k], (dict, list)) and isinstance(b[k], (dict, list)):
                        diffs.extend(_dict_diff(a[k], b[k], child_path))
                    else:
                        diffs.append(f"~ {child_path}: {json.dumps(a[k], ensure_ascii=False)[:50]} → {json.dumps(b[k], ensure_ascii=False)[:50]}")
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            child_path = f"{path}[{i}]"
            if i >= len(a):
                diffs.append(f"+ {child_path}: {json.dumps(b[i], ensure_ascii=False)[:100]}")
            elif i >= len(b):
                diffs.append(f"- {child_path}: {json.dumps(a[i], ensure_ascii=False)[:100]}")
            elif a[i] != b[i]:
                diffs.append(f"~ {child_path}: {json.dumps(a[i], ensure_ascii=False)[:50]} → {json.dumps(b[i], ensure_ascii=False)[:50]}")
    else:
        diffs.append(f"~ {path}: {json.dumps(a, ensure_ascii=False)[:50]} → {json.dumps(b, ensure_ascii=False)[:50]}")
    return diffs


# ──────────────────────────────────────────────
#  统计工具函数
# ──────────────────────────────────────────────
def _compute_stats(buffer: deque) -> dict:
    """对 deque[(x, val), ...] 计算基础统计量（纯 Python，不依赖 numpy）"""
    if not buffer:
        return {}
    vals = [p[1] for p in buffer]
    n = len(vals)
    mean = sum(vals) / n
    variance = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    delta = vals[-1] - vals[0] if n >= 2 else 0.0
    return {
        'current': vals[-1],
        'min': min(vals),
        'max': max(vals),
        'mean': mean,
        'std': variance ** 0.5,
        'count': n,
        'delta': delta,
    }


# ──────────────────────────────────────────────
#  计算字段表达式解析器（安全求值）
# ──────────────────────────────────────────────
import ast
import operator as _op

# 匹配裸标识符（field_name）—— 用于替换为安全变量名
_FIELD_RE = re.compile(r'[a-zA-Z_]\w*')
# 匹配点号路径（data.volt、sensor.temp 等，至少一个点）
_DOT_PATH_RE = re.compile(r'(?<![\w.])([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)+)(?![\w.])')

_SAFE_OPS = {
    ast.Add: _op.add, ast.Sub: _op.sub,
    ast.Mult: _op.mul, ast.Div: _op.truediv,
    ast.USub: _op.neg, ast.UAdd: _op.pos,
    ast.Pow: _op.pow, ast.Mod: _op.mod,
}

# Python 关键字和内置常量，不作为字段引用处理
_KEYWORDS = frozenset({'True', 'False', 'None', 'and', 'or', 'not', 'if', 'else'})


def _eval_ast(node, ctx: dict):
    """递归安全求值 AST 节点"""
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        raise NameError(f"未定义变量: {node.id}")
    elif isinstance(node, ast.BinOp):
        left = _eval_ast(node.left, ctx)
        right = _eval_ast(node.right, ctx)
        op_type = type(node.op)
        if op_type in _SAFE_OPS:
            return _SAFE_OPS[op_type](left, right)
        raise ValueError(f"不支持的运算符: {op_type.__name__}")
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_ast(node.operand, ctx)
        op_type = type(node.op)
        if op_type in _SAFE_OPS:
            return _SAFE_OPS[op_type](operand)
        raise ValueError(f"不支持的一元运算符: {op_type.__name__}")
    else:
        raise ValueError(f"不支持的语法节点: {type(node).__name__}")


def _resolve_field_value(name: str, field_values: dict, json_obj: dict | None) -> float | None:
    """解析字段名为数值。查找顺序：
    1) field_values 精确匹配
    2) field_values 后缀匹配（'volt' 匹配 key='data.volt'）
    3) 从原始 JSON 对象直接提取（_extract_json_path）
    """
    # 1) 精确匹配
    if name in field_values and isinstance(field_values[name], (int, float)):
        return float(field_values[name])
    # 2) 后缀匹配
    suffix = f".{name}"
    for k, v in field_values.items():
        if isinstance(k, str) and k.endswith(suffix) and isinstance(v, (int, float)):
            return float(v)
    # 3) 从 JSON 对象提取
    if json_obj is not None:
        val = _extract_json_path(json_obj, name)
        if val is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def eval_computed(expr: str, field_values: dict, json_obj: dict | None = None) -> float | None:
    """安全求值计算字段表达式。

    支持: + - * / ** % ( )  引用: field_name 或 data.volt
    例: "temperature * 9 / 5 + 32"  或  "volt * 100"

    查找顺序：field_values（已跟踪）→ 原始 JSON（无需跟踪源字段）
    """
    if not expr or not expr.strip():
        return None
    try:
        # ── 步骤1：替换点号路径（如 data.volt），优先于裸标识符 ──
        ctx = {}
        worked = expr

        def _replace_dot(m: re.Match) -> str:
            name = m.group(1)
            if name in _KEYWORDS:
                return name
            val = _resolve_field_value(name, field_values, json_obj)
            if val is not None:
                key = f"__f{len(ctx)}__"
                ctx[key] = float(val)
                return key
            return name  # 未找到，保留原文（后续 AST 解析会报错被捕获）

        worked = _DOT_PATH_RE.sub(_replace_dot, worked)

        # ── 步骤2：替换裸标识符（跳过已替换的 __fN__ 和关键字）──
        def _replace_bare(m: re.Match) -> str:
            name = m.group(0)
            if name in _KEYWORDS or name.startswith('__f'):
                return name
            val = _resolve_field_value(name, field_values, json_obj)
            if val is not None:
                key = f"__f{len(ctx)}__"
                ctx[key] = float(val)
                return key
            return name

        worked = _FIELD_RE.sub(_replace_bare, worked)

        # ── 步骤3：AST 安全求值 ──
        tree = ast.parse(worked, mode='eval')
        result = _eval_ast(tree.body, ctx)
        return float(result) if result is not None else None
    except Exception:
        return None


# ──────────────────────────────────────────────
#  线性回归（用于散点图趋势线）
# ──────────────────────────────────────────────
def _linear_regression(xs: list, ys: list) -> tuple | None:
    """简单线性回归 y = a + b*x，返回 (a, b, r_squared) 或 None"""
    n = len(xs)
    if n < 2:
        return None
    try:
        mx = sum(xs) / n
        my = sum(ys) / n
        ss_xy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        ss_xx = sum((x - mx) ** 2 for x in xs)
        if ss_xx == 0:
            return None
        b = ss_xy / ss_xx
        a = my - b * mx
        # R²
        ss_yy = sum((y - my) ** 2 for y in ys)
        ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
        r2 = 1 - (ss_res / ss_yy) if ss_yy > 0 else 0
        return (a, b, r2)
    except Exception:
        return None


# ──────────────────────────────────────────────
#  测试入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 模拟测试数据流
    dlg = JsonViewerDialog()

    def simulate_data():
        import random
        for _ in range(20):
            obj = {
                "temperature": round(random.uniform(20, 35), 2),
                "humidity": round(random.uniform(30, 80), 1),
                "pressure": round(random.uniform(990, 1030), 1),
                "timestamp": time.time(),
                "nested": {"x": random.randint(0, 100), "y": random.randint(0, 100)},
                "items": [random.randint(1, 10) for _ in range(random.randint(1, 5))],
            }
            dlg.feed_raw_data((json.dumps(obj) + '\n').encode('utf-8'))

    dlg._start_capture()
    simulate_data()
    QTimer.singleShot(500, simulate_data)

    dlg.show()
    app.exec_()
