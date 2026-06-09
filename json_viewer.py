#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JSON 数据分析面板 — 独立模块
功能：实时捕获串口/网络 JSON 数据流，列表/树形/表格/图表展示分析

依赖：PyQt5, pyqtgraph, numpy（标准库之外）
"""

import sys
import os
import json
import re
import time
import datetime
from collections import deque
from typing import Optional

from PyQt5.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QCheckBox, QMessageBox,
    QSplitter, QLineEdit, QFrame,
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
    QPixmap, QStandardItemModel, QStandardItem, QCursor,
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
    """实时折线图跟踪器，支持多字段、多Y轴、阈值线"""

    def __init__(self, parent=None):
        super().__init__(parent)
        if not HAS_PYQTGRAPH:
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("⚠ 需要安装 pyqtgraph 和 numpy 以启用图表功能"))
            return

        self._paused = False
        self._tracked: dict[str, dict] = {}  # path -> {chip, curve, buffer, viewbox, ...}
        self._color_idx = 0

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
        plot_bg = '#2C313C'
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground(plot_bg)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('bottom', '时间')
        self.plot_widget.addLegend()
        self.plot_widget.getPlotItem().setDownsampling(auto=True, mode='peak')
        # 启用鼠标交互
        self.plot_widget.setMouseTracking(True)

        self.empty_text = pg.TextItem('在上方树形视图拖拽字段或在下框输入路径，回车开始跟踪', color=(0xAB, 0xB2, 0xBF), anchor=(0.5, 0.5))
        self.empty_text.setFont(QFont("Microsoft YaHei", 10))
        self.plot_widget.addItem(self.empty_text)

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

        layout.addWidget(self.plot_widget, stretch=1)

        # 用事件过滤器让 PlotWidget 的拖放事件传递到 ChartTrackerWidget
        self.plot_widget.installEventFilter(self)

        # ── 控制按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setFont(QFont("Microsoft YaHei", 9))
        self.btn_pause.setToolTip("暂停/继续图表更新 (Space)")
        self.btn_pause.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self.btn_pause)

        self.btn_clear_chart = QPushButton("清空")
        self.btn_clear_chart.setFont(QFont("Microsoft YaHei", 9))
        self.btn_clear_chart.setToolTip("清空图表曲线数据（保留跟踪字段配置）")
        self.btn_clear_chart.clicked.connect(self.clear_all_data)
        btn_row.addWidget(self.btn_clear_chart)

        btn_row.addSpacing(8)

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
        """响应主题切换，更新绘图区背景"""
        if not HAS_PYQTGRAPH:
            return
        plot_bg = '#2C313C' if is_dark else '#FFFFFF'
        self.plot_widget.setBackground(plot_bg)
        text_color = (0xAB, 0xB2, 0xBF) if is_dark else (0x66, 0x66, 0x66)
        self.empty_text.setColor(text_color)

    # --- 字段管理 ---
    def add_field(self, path: str, alias: str = ""):
        if path in self._tracked:
            return False

        color = CURVE_COLORS[self._color_idx % len(CURVE_COLORS)]
        self._color_idx += 1

        chip = TrackChip(path, color, alias)
        chip.removed.connect(self._on_chip_removed)
        chip.threshold_changed.connect(self._update_threshold_lines)

        # 插入到输入框之前
        self.chips_layout.insertWidget(self.chips_layout.count() - 2, chip)

        # 创建曲线
        r, g, b = color
        pen = pg.mkPen(color=(r, g, b), width=1.5)
        curve = self.plot_widget.plot([], [], pen=pen, name=alias or path)

        self._tracked[path] = {
            'chip': chip,
            'curve': curve,
            'buffer': deque(maxlen=MAX_CURVE_POINTS),
            'alias': alias or path,
            'color': color,
        }

        self.empty_text.setVisible(False)
        # 有字段了，启动刷新定时器
        if not self.refresh_timer.isActive():
            self.refresh_timer.start()
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
        if not self._tracked:
            self.empty_text.setVisible(True)
            self.refresh_timer.stop()  # 无字段，停止刷新省资源

    def feed_data(self, obj: dict):
        """从新 JSON 对象中提取跟踪字段的值"""
        if self._paused or not self._tracked:
            return

        self._point_index += 1
        x = self._point_index

        for path, entry in self._tracked.items():
            try:
                val = _extract_json_path(obj, path)
                if val is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
                    entry['buffer'].append((x, val))
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
        for entry in self._tracked.values():
            entry['buffer'].clear()
            entry['curve'].setData([], [])
        self._point_index = 0

    # --- 内部控制 ---
    def _toggle_pause(self):
        self._paused = not self._paused
        self.btn_pause.setText("继续" if self._paused else "暂停")

    def _on_chip_removed(self, chip):
        self.remove_field(chip.path)

    def _on_add_field(self):
        path = self.edit_add_field.text().strip()
        if path:
            self.add_field(path)
            self.edit_add_field.clear()

    def _update_plot(self):
        if self._paused:
            return
        plot_width = self.plot_widget.width()

        for entry in self._tracked.values():
            buf = entry['buffer']
            if not buf:
                continue
            pts = list(buf)
            if len(pts) > plot_width:
                pts = lttb_downsample(pts, plot_width)
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
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
        """将 PlotWidget 的拖放事件转发给 ChartTrackerWidget"""
        if obj is self.plot_widget:
            if event.type() == QEvent.DragEnter:
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
#  JSON 详情查看器
# ──────────────────────────────────────────────
class DetailViewerWidget(QWidget):
    """JSON 详情查看器：树形 / 表格 / 原始文本 三视图切换"""

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
        self.combo_view.addItems(["树形", "表格", "原始文本"])
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
            for row in range(root.rowCount()):
                key_item = root.child(row, 0)
                val_item = root.child(row, 1)
                if key_item is None:
                    key_item = QStandardItem("")
                if val_item is None:
                    val_item = QStandardItem("")
                self.tree_model.appendRow([key_item, val_item])
            self.tree_view.expandToDepth(0)
        else:
            # 非 JSON 数据：树形中显示提示
            hint = QStandardItem("(非JSON数据，无可拖拽字段 —— 改为切换「原始文本」视图查看)")
            hint.setForeground(QColor(0x88, 0x88, 0x88))
            hint.setEditable(False)
            self.tree_model.appendRow([hint, QStandardItem(raw_text[:200])])

        # 表格
        if obj is not None:
            self._build_table(obj)
        else:
            self.table_view.clear()
            self.table_view.setRowCount(1)
            self.table_view.setColumnCount(1)
            self.table_view.setHorizontalHeaderLabels(["原始文本"])
            item = QTableWidgetItem(raw_text[:500])
            item.setFont(QFont("Consolas", 10))
            self.table_view.setItem(0, 0, item)

        # 原始文本
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
        act_chart = menu.addAction("📈 添加到图表跟踪")

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
        if is_dark:
            self.raw_view.setStyleSheet(
                "QPlainTextEdit { background-color: #2C313C; color: #ABB2BF; }"
            )
        else:
            self.raw_view.setStyleSheet(
                "QPlainTextEdit { background-color: #FFFFFF; color: #333333; }"
            )


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

    def __init__(self, parent=None):
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

        # 固定表格样式：确保文字在任何主题下清晰可读
        self._apply_table_style()
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
        self._apply_table_style(is_dark)

    def _apply_table_style(self, is_dark=True):
        """根据主题应用表格样式，确保文字/背景对比度清晰"""
        if is_dark:
            self.table.setStyleSheet("""
                QTableView {
                    background-color: #2C313C;
                    color: #ABB2BF;
                    border: 1px solid #3E4451;
                    gridline-color: #2C313C;
                    selection-background-color: #3E5A8C;
                    selection-color: #FFFFFF;
                    alternate-background-color: #252830;
                }
                QTableView::item { padding: 2px 6px; }
                QTableView::item:hover { background-color: rgba(82, 139, 255, 40); }
                QTableView::item:selected { background-color: #3E5A8C; color: #FFFFFF; }
                QHeaderView { background-color: #21252B; color: #8B95A5; border: none; }
                QHeaderView::section {
                    background-color: #21252B; color: #8B95A5;
                    border: 1px solid #2C313C; padding: 4px 6px; font-weight: bold;
                }
                QHeaderView::section:hover { background-color: #2C313C; }
            """)
        else:
            self.table.setStyleSheet("""
                QTableView {
                    background-color: #FFFFFF;
                    color: #333333;
                    border: 1px solid #CCCCCC;
                    gridline-color: #E0E0E0;
                    selection-background-color: #0078D4;
                    selection-color: #FFFFFF;
                    alternate-background-color: #F5F5F5;
                }
                QTableView::item { padding: 2px 6px; }
                QTableView::item:hover { background-color: rgba(0, 120, 212, 30); }
                QTableView::item:selected { background-color: #0078D4; color: #FFFFFF; }
                QHeaderView { background-color: #E8E8E8; color: #333333; border: none; }
                QHeaderView::section {
                    background-color: #E8E8E8; color: #333333;
                    border: 1px solid #CCCCCC; padding: 4px 6px; font-weight: bold;
                }
                QHeaderView::section:hover { background-color: #D0D0D0; }
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
    """后台线程：从 bytes 流中提取 JSON 对象"""

    MODE_JSON_OBJECT = 0     # 提取 JSON 对象（{...} 或 [...]）
    MODE_LINE_BY_LINE = 1    # 逐行尝试解析
    MODE_REGEX = 2           # 自定义正则

    items_ready = pyqtSignal(list)  # [(raw_text, obj, summary, parse_error), ...]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False
        self.filter_mode = self.MODE_JSON_OBJECT
        self.custom_regex = ""       # 自定义正则模式
        self._queue: deque[bytes] = deque()
        self._queue_mutex = QMutex()
        self._byte_buffer = bytearray()
        self.bytes_scanned = 0       # 已扫描字节数（主线程可读）

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
        """模式3：自定义正则提取"""
        if not self.custom_regex:
            return self._extract_json_objects()  # 回退
        batch = []
        text = self._byte_buffer.decode('utf-8', errors='replace')
        try:
            pattern = re.compile(self.custom_regex)
            last_end = 0
            for m in pattern.finditer(text):
                raw = m.group()
                batch.append(self._try_parse(raw))
                last_end = max(last_end, m.end())
            if last_end > 0:
                self._byte_buffer = bytearray(text[last_end:].encode('utf-8'))
            elif len(self._byte_buffer) > 256 * 1024:
                self._byte_buffer = self._byte_buffer[-65536:]
        except re.error:
            # 正则无效，回退到 JSON 对象提取
            return self._extract_json_objects()

        return batch


# ──────────────────────────────────────────────
#  主对话框
# ──────────────────────────────────────────────
class JsonViewerDialog(QDialog):
    """JSON 数据分析面板 — 主对话框"""

    def __init__(self, parent=None, theme_callback=None, arrow_paths=None):
        super().__init__(parent)
        self._theme_callback = theme_callback      # 父窗口的 _apply_dialog_theme 方法
        self._arrow_paths = arrow_paths or {}      # 箭头图标路径
        self._capture_running = False
        self._capture_count = 0
        self._capture_rate = 0
        self._rate_window = deque(maxlen=20)       # 速率滑动窗口（每秒）
        self._seq_counter = 0

        self.setWindowTitle("JSON 数据分析面板")
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

        # ──── 顶部控制栏 ────
        ctrl_widget = QWidget()
        ctrl_layout = QHBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(4, 2, 4, 2)
        ctrl_layout.setSpacing(6)

        # ─ 数据源 ─
        src_label = QLabel("监听源:")
        src_label.setFont(QFont("Microsoft YaHei", 9))
        ctrl_layout.addWidget(src_label)
        self.combo_source = QComboBox()
        self.combo_source.addItems(["当前串口", "UDP", "TCP Client", "TCP Server"])
        self.combo_source.setFont(QFont("Microsoft YaHei", 9))
        self.combo_source.setToolTip("选择数据来源")
        ctrl_layout.addWidget(self.combo_source)

        # 分隔
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setFrameShadow(QFrame.Sunken)
        ctrl_layout.addWidget(sep1)

        # ─ 过滤 ─
        flt_label = QLabel("过滤:")
        flt_label.setFont(QFont("Microsoft YaHei", 9))
        ctrl_layout.addWidget(flt_label)
        self.combo_filter_mode = QComboBox()
        self.combo_filter_mode.addItems(["提取 JSON 对象", "所有行尝试解析", "自定义正则"])
        self.combo_filter_mode.setFont(QFont("Microsoft YaHei", 9))
        self.combo_filter_mode.setToolTip("选择数据解析策略")
        ctrl_layout.addWidget(self.combo_filter_mode)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("搜索...")
        self.edit_search.setFont(QFont("Microsoft YaHei", 9))
        self.edit_search.setMaximumWidth(180)
        self.edit_search.setToolTip("输入关键字实时过滤列表 (Ctrl+F)")
        ctrl_layout.addWidget(self.edit_search)

        # 分隔
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFrameShadow(QFrame.Sunken)
        ctrl_layout.addWidget(sep2)

        # ─ 操作按钮 ─
        self.btn_start = QPushButton("开始监听")
        self.btn_start.setFont(QFont("Microsoft YaHei", 9))
        self.btn_start.setToolTip("开始从数据源捕获 JSON")
        self.btn_start.clicked.connect(self._start_capture)
        ctrl_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setFont(QFont("Microsoft YaHei", 9))
        self.btn_stop.setEnabled(False)
        self.btn_stop.setToolTip("停止捕获")
        self.btn_stop.clicked.connect(self._stop_capture)
        ctrl_layout.addWidget(self.btn_stop)

        self.btn_clear = QPushButton("清空全部")
        self.btn_clear.setFont(QFont("Microsoft YaHei", 9))
        self.btn_clear.setToolTip("清空所有捕获数据和图表")
        self.btn_clear.clicked.connect(self._clear_all)
        ctrl_layout.addWidget(self.btn_clear)

        # ─ 使用说明 ─
        self.btn_help = QPushButton("使用说明")
        self.btn_help.setFont(QFont("Microsoft YaHei", 9))
        self.btn_help.setToolTip("查看 JSON 面板使用帮助")
        self.btn_help.clicked.connect(self._show_help)
        ctrl_layout.addWidget(self.btn_help)

        ctrl_layout.addStretch()

        # ─ 状态指示 ─
        self.lbl_status = QLabel("状态: 就绪 | 捕获 0 条 | 速率 0/s")
        self.lbl_status.setFont(QFont("Consolas", 10))
        self.lbl_status.setTextFormat(Qt.PlainText)
        self.lbl_status.setMinimumWidth(280)
        self.lbl_status.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(128,128,128,0.12);"
            "  border-radius: 4px;"
            "  padding: 3px 10px;"
            "}"
        )
        ctrl_layout.addWidget(self.lbl_status)

        main_layout.addWidget(ctrl_widget)

        # ──── 主分割器（左右） ────
        self.splitter_h = QSplitter(Qt.Horizontal)

        # 左侧：捕获列表
        self.capture_list = CaptureListWidget()
        self.splitter_h.addWidget(self.capture_list)

        # 右侧：垂直分割器（详情 + 图表）
        self.splitter_v = QSplitter(Qt.Vertical)

        self.chart_tracker = ChartTrackerWidget()
        self.detail_viewer = DetailViewerWidget(chart_tracker=self.chart_tracker)
        self.detail_viewer._export_all_callback = self._export_all_jsonl

        self.splitter_v.addWidget(self.detail_viewer)   # 上半：详情查看器
        self.splitter_v.addWidget(self.chart_tracker)    # 下半：图表跟踪器

        self.splitter_h.addWidget(self.splitter_v)

        # 初始比例 左:右 = 35:65
        self.splitter_h.setSizes([420, 780])
        self.splitter_v.setSizes([400, 350])

        main_layout.addWidget(self.splitter_h, stretch=1)

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

        # 速率定时器
        self._rate_timer = QTimer(self)
        self._rate_timer.setInterval(1000)
        self._rate_timer.timeout.connect(self._update_rate)
        self._rate_timer.start()

    def set_theme(self, is_dark: bool):
        """响应外部主题切换，更新所有子组件配色"""
        self.capture_list.set_theme(is_dark)
        self.chart_tracker.set_theme(is_dark)
        self.detail_viewer.set_theme(is_dark)

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

    def _show_help(self):
        """显示使用说明"""
        QMessageBox.information(self, "JSON 数据分析面板 — 使用说明",
            "<b>基本操作</b><br>"
            "1. 打开串口连接后，点击 <b>「开始监听」</b> 开始捕获<br>"
            "2. 根据数据格式选择过滤模式：<br>"
            "&nbsp;&nbsp;&nbsp;<b>提取 JSON 对象</b> — 标准 {'{...}'} / {'[...]'} 格式<br>"
            "&nbsp;&nbsp;&nbsp;<b>所有行尝试解析</b> — 逐行捕获（含非JSON文本）<br>"
            "&nbsp;&nbsp;&nbsp;<b>自定义正则</b> — 用正则表达式匹配<br>"
            "3. 点击左侧列表项，右侧显示详情（树形/表格/原始文本）<br>"
            "4. 搜索框可实时过滤列表内容<br><br>"
            "<b>图表跟踪</b><br>"
            "• 从树形视图中 <b>拖拽数值字段到图表区</b> 即可添加跟踪<br>"
            "• 或在图表上方输入框中输入字段路径，回车添加<br>"
            "• 右键跟踪标签可设置别名、阈值、颜色<br>"
            "• 鼠标悬停图表显示数据值，滚轮缩放<br><br>"
            "<b>快捷键</b><br>"
            "Ctrl+F — 聚焦搜索框 &nbsp;|&nbsp; Delete — 删除选中项<br>"
            "Space — 暂停/继续图表 &nbsp;|&nbsp; Ctrl+S — 导出全部"
        )

    def _export_all_jsonl(self):
        """导出全部已捕获的 JSON 为 .jsonl 文件"""
        path, _ = QFileDialog.getSaveFileName(self, "导出全部 JSONL", "all_data.jsonl", "JSONL (*.jsonl)")
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                for item in self.capture_list.model._items:
                    f.write(item.get('raw', '') + '\n')

    def _update_rate(self):
        """每秒更新速率显示 + 诊断提示"""
        # 未启动时不更新（保持 feed_raw_data 设置的提示）
        if not self._capture_running:
            return

        now = time.time()
        while self._rate_window and now - self._rate_window[0] > 10:
            self._rate_window.popleft()
        rate = len(self._rate_window) / 10.0 if self._rate_window else 0

        # 诊断：有数据流入但没有提取到 JSON 时显示提示
        hint = ""
        if self._capture_count == 0:
            scanned = self._capture_thread.bytes_scanned
            if scanned > 100:
                hint = "  |  [!] 数据非JSON格式，请切换过滤模式为「所有行尝试解析」"
                self._set_status_style(color="#E5C07B", bold=True)
            else:
                self._set_status_style()
        else:
            self._set_status_style()

        self.lbl_status.setText(
            f"状态: 正在捕获 | 捕获 {self._capture_count} 条 | 速率 {rate:.1f}/s{hint}"
        )

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
        self._capture_count = 0
        self._seq_counter = 0
        self._rate_window.clear()
        self.capture_list.clear()
        self.chart_tracker.clear_all_data()
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
            # 保存跟踪字段列表
            ini['tracks'] = {
                'paths': json.dumps(list(self.chart_tracker._tracked.keys())),
                'aliases': json.dumps({p: e['chip'].alias for p, e in self.chart_tracker._tracked.items()}),
            }
            with open('json_viewer.ini', 'w', encoding='utf-8') as f:
                ini.write(f)
        except Exception:
            pass

    def _restore_layout(self):
        try:
            import configparser
            ini = configparser.ConfigParser()
            if not ini.read('json_viewer.ini'):
                return
            if 'layout' in ini:
                h = QByteArray.fromHex(ini['layout']['splitter_h'].encode('utf-8'))
                v = QByteArray.fromHex(ini['layout']['splitter_v'].encode('utf-8'))
                self.splitter_h.restoreState(h)
                self.splitter_v.restoreState(v)
        except Exception:
            pass

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
