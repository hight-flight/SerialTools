# -*- coding: utf-8 -*-
"""
主题模块：颜色常量、QSS 样式表、对话框主题应用工具函数。
"""

VERSION = "1.4.0"

import platform
import re

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication, QMessageBox, QPushButton, QStyle, QStyleOptionButton,
)


_TEXT_ESCAPE_PATTERN = re.compile(
    r"\\(?:[\\rntbfva]|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})"
)


def unescape_text(text):
    """解析受支持的字面转义，同时保持普通 Unicode 文本不变。"""
    simple_escapes = {
        r"\\": "\\", r"\r": "\r", r"\n": "\n", r"\t": "\t",
        r"\b": "\b", r"\f": "\f", r"\v": "\v", r"\a": "\a",
    }

    def replace_escape(match):
        token = match.group(0)
        if token in simple_escapes:
            return simple_escapes[token]
        digit_counts = {"x": 2, "u": 4, "U": 8}
        prefix = token[1]
        digits = token[2:2 + digit_counts[prefix]]
        try:
            return chr(int(digits, 16))
        except (ValueError, OverflowError):
            return token

    return _TEXT_ESCAPE_PATTERN.sub(replace_escape, text)


class DataReceiver(QObject):
    """共享数据接收器：用于子对话框解耦串口数据接收信号。"""
    data_received = pyqtSignal(bytes)

# --- 主题颜色常量 ---
THEME_COLORS = {
    'light': {
        'text_normal':    QColor(0x1D, 0x29, 0x39),
        'text_send':      QColor(0x16, 0x77, 0xFF),
        'text_system':    QColor(0x66, 0x70, 0x85),
        'text_error':     QColor(0xDC, 0x3E, 0x42),
        'highlight_bg':   QColor(207, 229, 255, 160),  # 明亮模式柔和蓝色高亮
        'ansi_default_fg': QColor(0x1D, 0x29, 0x39),
        # ANSI 前景色映射（亮色模式使用原色）
        'ansi_fg': {
            '30': QColor(0, 0, 0),       '31': QColor(255, 0, 0),
            '32': QColor(0, 128, 0),     '33': QColor(165, 42, 42),
            '34': QColor(0, 0, 255),     '35': QColor(128, 0, 128),
            '36': QColor(0, 128, 128),   '37': QColor(128, 128, 128),
            '90': QColor(128, 128, 128), '91': QColor(255, 0, 0),
            '92': QColor(0, 128, 0),     '93': QColor(165, 42, 42),
            '94': QColor(0, 0, 255),     '95': QColor(128, 0, 128),
            '96': QColor(0, 128, 128),   '97': QColor(200, 200, 200),
        },
        # ANSI 背景色映射
        'ansi_bg': {
            '40': QColor(0, 0, 0),       '41': QColor(255, 0, 0),
            '42': QColor(0, 255, 0),     '43': QColor(255, 255, 0),
            '44': QColor(0, 0, 255),     '45': QColor(255, 0, 255),
            '46': QColor(0, 255, 255),   '47': QColor(255, 255, 255),
        },
    },
    'dark': {
        'text_normal':    QColor(0xE6, 0xED, 0xF5),
        'text_send':      QColor(0x49, 0xA6, 0xFF),
        'text_system':    QColor(0x9E, 0xB0, 0xC3),
        'text_error':     QColor(0xFF, 0x7B, 0x7F),
        'highlight_bg':   QColor(36, 91, 143, 160),   # 暗黑模式数据面板蓝色高亮
        'ansi_default_fg': QColor(0xE6, 0xED, 0xF5),
        # ANSI 前景色映射（暗底提亮）
        'ansi_fg': {
            '30': QColor(0x6A, 0x73, 0x84),  '31': QColor(0xE0, 0x6C, 0x75),
            '32': QColor(0x98, 0xC3, 0x79),  '33': QColor(0xD1, 0x9A, 0x66),
            '34': QColor(0x61, 0xAF, 0xEF),  '35': QColor(0xC6, 0x78, 0xDD),
            '36': QColor(0x56, 0xB6, 0xC2),  '37': QColor(0xAB, 0xB2, 0xBF),
            '90': QColor(0x5C, 0x63, 0x70),  '91': QColor(0xE0, 0x6C, 0x75),
            '92': QColor(0x98, 0xC3, 0x79),  '93': QColor(0xD1, 0x9A, 0x66),
            '94': QColor(0x61, 0xAF, 0xEF),  '95': QColor(0xC6, 0x78, 0xDD),
            '96': QColor(0x56, 0xB6, 0xC2),  '97': QColor(0xDC, 0xDF, 0xE4),
        },
        # ANSI 背景色映射
        'ansi_bg': {
            '40': QColor(0x2C, 0x31, 0x3C),  '41': QColor(0xBE, 0x50, 0x46),
            '42': QColor(0x50, 0x8C, 0x50),  '43': QColor(0x8C, 0x8C, 0x46),
            '44': QColor(0x46, 0x46, 0xBE),  '45': QColor(0xBE, 0x46, 0xBE),
            '46': QColor(0x46, 0x8C, 0x8C),  '47': QColor(0x6A, 0x73, 0x84),
        },
    },
}

DARK_QSS = """
QMainWindow        { background-color: #282C34; }
QMenuBar           { background-color: #282C34; color: #ABB2BF; border-bottom: 1px solid #3E4451; }
QMenuBar::item:selected { background-color: #3E4451; }
QGroupBox {
    border: 1px solid #3E4451; border-radius: 6px;
    margin-top: 8px; padding-top: 8px; font-weight: bold; color: #ABB2BF;
    background: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 12px; padding: 0 8px; color: #ABB2BF;
}
QTextEdit {
    background-color: #2C313C; color: #ABB2BF;
    border: 1px solid #3E4451; border-radius: 4px;
    selection-background-color: #3E4451;
}
QTextEdit:focus { border-color: #528BFF; }
QLineEdit {
    background-color: #2C313C; color: #ABB2BF;
    border: 1px solid #3E4451; border-radius: 4px;
    selection-background-color: #3E4451;
}
QLineEdit:focus { border-color: #528BFF; }
QLineEdit:read-only { background-color: #21252B; }
QLineEdit:disabled { background-color: #21252B; color: #9AA2B4; }
QComboBox {
    background-color: #2C313C; color: #ABB2BF;
    border: 1px solid #3E4451; border-radius: 4px; padding: 4px 8px;
}
QComboBox:hover { border-color: #528BFF; }
QComboBox:focus { border-color: #528BFF; }
QComboBox:disabled { background-color: #21252B; color: #9AA2B4; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(__ARROW_DARK__); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background-color: #21252B; color: #ABB2BF;
    selection-background-color: #3E4451; border: 1px solid #181A1F;
    outline: none;
}
QPushButton {
    background-color: #2C313C; color: #E6EAF0;
    border: 1px solid #4B5363; border-radius: 4px; padding: 4px 12px;
}
QPushButton:hover    { background-color: #3E4451; }
QPushButton:pressed  { background-color: #21252B; }
QPushButton:checked  { background-color: #528BFF; color: #FFFFFF; }
QPushButton:checked:hover { background-color: #61AFEF; }
QPushButton:focus { border: 2px solid #61AFEF; padding: 3px 11px; }
QPushButton:disabled { background-color: #21252B; color: #9AA2B4; }
QPushButton[danger="true"] {
    background-color: #5C2B31; color: #FFB3B8; border-color: #A64B55;
}
QPushButton[danger="true"]:hover { background-color: #71343C; color: #FFFFFF; }
QCheckBox   { color: #ABB2BF; spacing: 6px; }
QCheckBox:focus { color: #FFFFFF; }
QCheckBox:disabled { color: #9AA2B4; }
QCheckBox::indicator {
    background-color: #2C313C; border: 1px solid #3E4451;
    border-radius: 3px; width: 16px; height: 16px;
}
QCheckBox::indicator:hover { border-color: #528BFF; }
QCheckBox::indicator:checked { background-color: #528BFF; border-color: #528BFF; }
QCheckBox::indicator:disabled { background-color: #21252B; border-color: #2C313C; }
QLabel      { color: #ABB2BF; }
QSpinBox {
    background-color: #2C313C; color: #E6EAF0;
    border: 1px solid #4B5363; border-radius: 4px; padding: 4px 4px;
}
QSpinBox:focus { border-color: #528BFF; }
QSpinBox:disabled { background-color: #21252B; color: #9AA2B4; }
QTableView, QTableWidget {
    background-color: #2C313C; color: #E6EAF0;
    border: 1px solid #4B5363; gridline-color: #4B5363;
    selection-background-color: #3E4451;
    alternate-background-color: rgba(44, 49, 60, 180);
}
QTableView::viewport, QTableWidget::viewport {
    background-color: #2C313C;
}
QTableView::item:hover, QTableWidget::item:hover {
    background-color: rgba(82, 139, 255, 50);
    color: #ABB2BF;
}
QHeaderView { background-color: #21252B; }
QHeaderView::section {
    background-color: #21252B; color: #E6EAF0;
    border: 1px solid #4B5363; padding: 4px;
}
QHeaderView::section:hover { background-color: #2C313C; }
QTableCornerButton::section {
    background-color: #21252B; border: 1px solid #3E4451;
}
QStatusBar  { background-color: #21252B; border-top: 1px solid #181A1F; color: #6A7384; }
QStatusBar::item { border: none; }
QSplitter::handle { background-color: #3E4451; }
QSplitter::handle:hover { background-color: #4B5363; }
QSplitter::handle:pressed { background-color: #5C6370; }
QScrollArea { background-color: #282C34; border: none; }
QScrollArea > QWidget > QWidget { background-color: #282C34; }
QScrollBar:vertical {
    background-color: #282C34; width: 12px; border-radius: 6px; margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #3E4451; border-radius: 6px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background-color: #4B5363; }
QScrollBar::handle:vertical:pressed { background-color: #5C6370; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background-color: #282C34; height: 12px; border-radius: 6px; margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #3E4451; border-radius: 6px; min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background-color: #4B5363; }
QScrollBar::handle:horizontal:pressed { background-color: #5C6370; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QMenu {
    background-color: #21252B; color: #ABB2BF;
    border: 1px solid #181A1F; padding: 4px 0;
}
QMenu::item { padding: 8px 28px 8px 16px; }
QMenu::item:selected { background-color: #3E4451; }
QMenu::separator { height: 1px; background-color: #3E4451; margin: 4px 8px; }
QProgressBar {
    background-color: #2C313C; border: 1px solid #3E4451;
    border-radius: 4px; color: #ABB2BF; text-align: center;
}
QProgressBar::chunk { background-color: #528BFF; border-radius: 3px; }
QDialog    { background-color: #282C34; color: #ABB2BF; }
QSlider::groove:horizontal {
    background-color: #3E4451; height: 6px; border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: #528BFF; width: 14px; height: 14px;
    margin: -4px 0; border-radius: 8px;
}
QSlider::handle:horizontal:hover { background-color: #61AFEF; }
QSlider::sub-page:horizontal { background-color: #528BFF; border-radius: 3px; }
QToolTip {
    background-color: #21252B; color: #ABB2BF;
    border: 1px solid #3E4451; border-radius: 4px; padding: 4px 8px;
}
QTabWidget::pane { border: 1px solid #3E4451; background-color: #282C34; }
QTabBar::tab {
    background-color: #21252B; color: #ABB2BF;
    padding: 6px 16px; border: 1px solid #3E4451;
    border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected { background-color: #282C34; }
QTabBar::tab:hover { background-color: #3E4451; }
"""

LIGHT_QSS = """
QMainWindow        { background-color: #F5F5F5; }
QMenuBar           { background-color: #F0F0F0; color: #333333; border-bottom: 1px solid #DDDDDD; }
QMenuBar::item:selected { background-color: #DDDDDD; }
QGroupBox {
    border: 1px solid #DDDDDD; border-radius: 6px;
    margin-top: 8px; padding-top: 8px; font-weight: bold; color: #444444;
    background: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 12px; padding: 0 8px; color: #444444;
}
QTextEdit {
    background-color: rgba(255, 255, 255, 230); color: #333333;
    border: 1px solid #CCCCCC; border-radius: 4px;
    selection-background-color: #0078D4;
}
QTextEdit:focus { border-color: #005A9E; }
QLineEdit {
    background-color: rgba(255, 255, 255, 230); color: #333333;
    border: 1px solid #CCCCCC; border-radius: 4px;
    selection-background-color: #0078D4;
}
QLineEdit:focus { border-color: #005A9E; }
QLineEdit:read-only { background-color: #F0F0F0; }
QLineEdit:disabled { background-color: #F0F0F0; color: #666666; }
QComboBox {
    background-color: rgba(255, 255, 255, 230); color: #333333;
    border: 1px solid #CCCCCC; border-radius: 4px; padding: 4px 8px;
}
QComboBox:hover { border-color: #0078D4; }
QComboBox:focus { border-color: #005A9E; }
QComboBox:disabled { background-color: #F0F0F0; color: #767676; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(__ARROW_LIGHT__); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background-color: rgba(255, 255, 255, 240); color: #333333;
    selection-background-color: #0078D4; border: 1px solid #CCCCCC;
    outline: none;
}
QPushButton {
    background-color: #E0E0E0; color: #333333;
    border: 1px solid #CCCCCC; border-radius: 4px; padding: 4px 12px;
}
QPushButton:hover    { background-color: #D0D0D0; }
QPushButton:pressed  { background-color: #C0C0C0; }
QPushButton:checked  { background-color: #0078D4; color: #FFFFFF; }
QPushButton:checked:hover { background-color: #168BE0; }
QPushButton:focus { border: 2px solid #005A9E; padding: 3px 11px; }
QPushButton:disabled { background-color: #F0F0F0; color: #666666; }
QPushButton[danger="true"] {
    background-color: #FDE7E9; color: #A4262C; border-color: #D13438;
}
QPushButton[danger="true"]:hover { background-color: #F8D7DA; }
QCheckBox   { color: #333333; spacing: 6px; }
QCheckBox:focus { color: #005A9E; }
QCheckBox:disabled { color: #666666; }
QCheckBox::indicator {
    background-color: #FFFFFF; border: 1px solid #AAAAAA;
    border-radius: 3px; width: 16px; height: 16px;
}
QCheckBox::indicator:hover { border-color: #0078D4; }
QCheckBox::indicator:checked { background-color: #0078D4; border-color: #0078D4; }
QCheckBox::indicator:disabled { background-color: #F0F0F0; border-color: #CCCCCC; }
QLabel      { color: #333333; }
QSpinBox {
    background-color: rgba(255, 255, 255, 230); color: #333333;
    border: 1px solid #CCCCCC; border-radius: 4px; padding: 4px 4px;
}
QSpinBox:focus { border-color: #005A9E; }
QSpinBox:disabled { background-color: #F0F0F0; color: #767676; }
QTableView, QTableWidget {
    background-color: rgba(255, 255, 255, 230); color: #333333;
    border: 1px solid #CCCCCC; gridline-color: #DDDDDD;
    selection-background-color: #0078D4;
    alternate-background-color: rgba(245, 245, 245, 210);
}
QTableView::viewport, QTableWidget::viewport {
    background-color: rgba(255, 255, 255, 230);
}
QTableView::item:hover, QTableWidget::item:hover {
    background-color: rgba(0, 120, 212, 60);
    color: #333333;
}
QHeaderView { background-color: #E8E8E8; }
QHeaderView::section {
    background-color: #E8E8E8; color: #333333;
    border: 1px solid #CCCCCC; padding: 4px;
}
QHeaderView::section:hover { background-color: #D0D0D0; }
QTableCornerButton::section {
    background-color: #E8E8E8; border: 1px solid #CCCCCC;
}
QStatusBar  { background-color: #E8E8E8; border-top: 1px solid #CCCCCC; color: #333333; }
QStatusBar::item { border: none; }
QSplitter::handle { background-color: #CCCCCC; }
QSplitter::handle:hover { background-color: #AAAAAA; }
QSplitter::handle:pressed { background-color: #999999; }
QScrollArea { background-color: #F5F5F5; border: none; }
QScrollArea > QWidget > QWidget { background-color: #F5F5F5; }
QScrollBar:vertical {
    background-color: #F5F5F5; width: 12px; border-radius: 6px; margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #C0C0C0; border-radius: 6px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background-color: #A0A0A0; }
QScrollBar::handle:vertical:pressed { background-color: #888888; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background-color: #F5F5F5; height: 12px; border-radius: 6px; margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #C0C0C0; border-radius: 6px; min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background-color: #A0A0A0; }
QScrollBar::handle:horizontal:pressed { background-color: #888888; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QMenu {
    background-color: #FFFFFF; color: #333333;
    border: 1px solid #CCCCCC; padding: 4px 0;
}
QMenu::item { padding: 8px 28px 8px 16px; }
QMenu::item:selected { background-color: #0078D4; color: #FFFFFF; }
QMenu::separator { height: 1px; background-color: #DDDDDD; margin: 4px 8px; }
QProgressBar {
    background-color: #E8E8E8; border: 1px solid #CCCCCC;
    border-radius: 4px; color: #333333; text-align: center;
}
QProgressBar::chunk { background-color: #0078D4; border-radius: 3px; }
QDialog    { background-color: #F5F5F5; color: #333333; }
QSlider::groove:horizontal {
    background-color: #DDDDDD; height: 6px; border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: #0078D4; width: 14px; height: 14px;
    margin: -4px 0; border-radius: 8px;
}
QSlider::handle:horizontal:hover { background-color: #106EBE; }
QSlider::sub-page:horizontal { background-color: #0078D4; border-radius: 3px; }
QToolTip {
    background-color: #FFFFFF; color: #333333;
    border: 1px solid #CCCCCC; border-radius: 4px; padding: 4px 8px;
}
QTabWidget::pane { border: 1px solid #CCCCCC; background-color: #F5F5F5; }
QTabBar::tab {
    background-color: #E8E8E8; color: #333333;
    padding: 6px 16px; border: 1px solid #CCCCCC;
    border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected { background-color: #F5F5F5; }
QTabBar::tab:hover { background-color: #D0D0D0; }
"""


# 工业控制台 2.0：仅覆盖视觉令牌，不改变控件尺寸、布局或交互。
LIGHT_QSS += """
QMainWindow, QWidget { background-color: #F6F8FB; color: #1D2939; }
QMenuBar { background-color: #FFFFFF; color: #344054; border-bottom: 1px solid #E3E8EF; }
QMenuBar::item { padding: 6px 10px; border-radius: 4px; }
QMenuBar::item:selected { background-color: #EEF5FF; color: #1677FF; }
QMenu { background-color: #FFFFFF; color: #344054; border: 1px solid #E3E8EF; border-radius: 8px; }
QMenu::item:selected { background-color: #EEF5FF; color: #1677FF; }
QGroupBox { background-color: #FFFFFF; color: #1D2939; border: 1px solid #E3E8EF; border-radius: 8px; margin-top: 12px; padding-top: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; background-color: #FFFFFF; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background-color: #FFFFFF; color: #1D2939; border: 1px solid #D7DFEA; border-radius: 6px; selection-background-color: #CFE5FF; selection-color: #102A56; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #1677FF; }
QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only { background-color: #F8FAFC; }
QPushButton { background-color: #FFFFFF; color: #344054; border: 1px solid #D7DFEA; border-radius: 6px; padding: 4px 10px; }
QPushButton:hover { background-color: #F1F6FF; border-color: #8FC0FF; color: #1677FF; }
QPushButton:pressed { background-color: #E2EFFF; }
QPushButton:checked { background-color: #1677FF; color: #FFFFFF; border-color: #1677FF; }
QPushButton[primary="true"] { background-color: #16844A; color: #FFFFFF; border-color: #126B3D; font-weight: 600; }
QPushButton[primary="true"]:hover { background-color: #126B3D; }
QPushButton[danger="true"] { background-color: #C83B4D; color: #FFFFFF; border-color: #A92E3E; font-weight: 600; }
QPushButton[danger="true"]:hover { background-color: #A92E3E; }
QPushButton:focus { border: 2px solid #1677FF; }
QHeaderView::section { background-color: #F1F5F9; color: #475467; border: none; border-bottom: 1px solid #E3E8EF; padding: 5px 8px; }
QTableView, QTableWidget, QTreeView { background-color: #FFFFFF; alternate-background-color: #F8FAFC; color: #1D2939; gridline-color: #E8EDF3; border: 1px solid #E3E8EF; border-radius: 8px; }
QTableView::item:selected, QTableWidget::item:selected, QTreeView::item:selected { background-color: #E2EFFF; color: #102A56; }
QCheckBox, QRadioButton { color: #344054; spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:checked { background-color: #1677FF; border-color: #1677FF; image: url(__CHECK_LIGHT__); }
QRadioButton::indicator:checked { background-color: #1677FF; border-color: #1677FF; }
QCheckBox::indicator:checked:disabled { background-color: #9CBEE8; border-color: #9CBEE8; }
QTableWidget QCheckBox::indicator { width: 0px; height: 0px; border: none; }
QTableWidget QCheckBox::indicator:checked { image: url(__CHECK_LIGHT__); }
QStatusBar { background-color: #FFFFFF; color: #667085; border-top: 1px solid #E3E8EF; }
QSplitter::handle { background-color: #E3E8EF; }
QSplitter::handle:hover { background-color: #B9D8FF; }
QScrollArea, QScrollArea > QWidget > QWidget { background-color: #F6F8FB; }
QScrollBar:vertical { background: #F6F8FB; width: 10px; margin: 4px; }
QScrollBar::handle:vertical { background: #B9C5D3; min-height: 28px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #8FA1B5; }
"""

DARK_QSS += """
QMainWindow, QWidget { background-color: #151A22; color: #E6EDF5; }
QMenuBar { background-color: #1B2330; color: #C8D3E0; border-bottom: 1px solid #2D3A4A; }
QMenuBar::item { padding: 6px 10px; border-radius: 4px; }
QMenuBar::item:selected { background-color: #223C59; color: #78BCFF; }
QMenu { background-color: #202936; color: #D9E3EE; border: 1px solid #334155; border-radius: 8px; }
QMenu::item:selected { background-color: #223C59; color: #78BCFF; }
QGroupBox { background-color: #202936; color: #E6EDF5; border: 1px solid #2D3A4A; border-radius: 8px; margin-top: 12px; padding-top: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; background-color: #202936; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background-color: #19212C; color: #E6EDF5; border: 1px solid #3A4A5E; border-radius: 6px; selection-background-color: #245B8F; selection-color: #FFFFFF; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #49A6FF; }
QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only { background-color: #111827; }
QPlainTextEdit { font-family: Consolas, "Cascadia Mono"; }
QPushButton { background-color: #263241; color: #D9E3EE; border: 1px solid #3A4A5E; border-radius: 6px; padding: 4px 10px; }
QPushButton:hover { background-color: #2B4057; border-color: #49A6FF; color: #FFFFFF; }
QPushButton:pressed { background-color: #203A55; }
QPushButton:checked { background-color: #1677FF; color: #FFFFFF; border-color: #49A6FF; }
QPushButton[primary="true"] { background-color: #238653; color: #FFFFFF; border-color: #3DA76B; font-weight: 600; }
QPushButton[primary="true"]:hover { background-color: #2E9B63; }
QPushButton[danger="true"] { background-color: #A63D4B; color: #FFFFFF; border-color: #D05A68; font-weight: 600; }
QPushButton[danger="true"]:hover { background-color: #B94958; }
QPushButton:focus { border: 2px solid #49A6FF; }
QHeaderView::section { background-color: #263241; color: #B9C8D8; border: none; border-bottom: 1px solid #3A4A5E; padding: 5px 8px; }
QTableView, QTableWidget, QTreeView { background-color: #19212C; alternate-background-color: #1E2835; color: #E6EDF5; gridline-color: #2D3A4A; border: 1px solid #2D3A4A; border-radius: 8px; }
QTableView::item:selected, QTableWidget::item:selected, QTreeView::item:selected { background-color: #245B8F; color: #FFFFFF; }
QCheckBox, QRadioButton { color: #C8D3E0; spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:checked { background-color: #49A6FF; border-color: #49A6FF; image: url(__CHECK_DARK__); }
QRadioButton::indicator:checked { background-color: #49A6FF; border-color: #49A6FF; }
QCheckBox::indicator:checked:disabled { background-color: #415164; border-color: #53657A; }
QTableWidget QCheckBox::indicator { width: 0px; height: 0px; border: none; }
QTableWidget QCheckBox::indicator:checked { image: url(__CHECK_DARK__); }
QStatusBar { background-color: #1B2330; color: #9EB0C3; border-top: 1px solid #2D3A4A; }
QSplitter::handle { background-color: #2D3A4A; }
QSplitter::handle:hover { background-color: #415A77; }
QScrollArea, QScrollArea > QWidget > QWidget { background-color: #151A22; }
QScrollBar:vertical { background: #151A22; width: 10px; margin: 4px; }
QScrollBar::handle:vertical { background: #415164; min-height: 28px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #5A6D82; }
"""

def fit_message_box_buttons(dialog):
    """按实际字体宽度扩展消息框按钮，避免高 DPI 下文字被裁切。"""
    if not isinstance(dialog, QMessageBox):
        return

    for button in dialog.buttons():
        visible_text = button.text().replace("&", "")
        text_width = button.fontMetrics().horizontalAdvance(visible_text)
        button.setMinimumWidth(max(88, text_width + 32, button.sizeHint().width()))
        button.setMinimumHeight(max(30, button.sizeHint().height()))
        button.updateGeometry()

    if dialog.layout() is not None:
        dialog.layout().activate()
    dialog.adjustSize()


def fit_push_button_texts(root):
    """按按钮实际内容区域补足文字安全余量，兼容不同字体和 DPI。"""
    buttons = [root] if isinstance(root, QPushButton) else []
    buttons.extend(root.findChildren(QPushButton))

    for button in buttons:
        if not button.text():
            continue
        button.ensurePolished()
        option = QStyleOptionButton()
        button.initStyleOption(option)
        content_rect = button.style().subElementRect(
            QStyle.SE_PushButtonContents, option, button
        )
        visible_text = button.text().replace("&", "")
        required_width = button.fontMetrics().horizontalAdvance(visible_text) + 8
        if not button.icon().isNull():
            required_width += button.iconSize().width() + 6
        deficit = required_width - content_rect.width()
        if deficit > 0:
            button.setMinimumWidth(
                max(button.minimumWidth(), button.width() + deficit)
            )
            button.updateGeometry()


def apply_dialog_theme(dialog, is_dark):
    """为子对话框应用当前主题（暗黑/明亮），支持运行时切换。"""
    is_win = platform.system() == 'Windows'
    app = QApplication.instance()
    if app is not None and not app.windowIcon().isNull():
        dialog.setWindowIcon(app.windowIcon())

    if is_dark:
        # 暗黑主题
        dialog.setStyleSheet(QApplication.instance().styleSheet())
        p = dialog.palette()
        p.setColor(QPalette.Highlight, QColor('#3E4451'))
        p.setColor(QPalette.HighlightedText, QColor('#ABB2BF'))
        p.setColor(QPalette.Button, QColor('#21252B'))
        p.setColor(QPalette.ButtonText, QColor('#ABB2BF'))
        dialog.setPalette(p)
        # Windows DWM 标题栏暗色
        if is_win:
            try:
                import ctypes
                hwnd = int(dialog.winId())
                if hwnd:
                    value = ctypes.c_int(1)
                    for attr in (20, 19):
                        try:
                            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                                ctypes.wintypes.HWND(hwnd), ctypes.c_uint(attr),
                                ctypes.byref(value), ctypes.sizeof(value)) == 0:
                                break
                        except Exception:
                            continue
                    r, g, b = 0x15, 0x1A, 0x22
                    colorref = ctypes.c_uint32((b << 16) | (g << 8) | r)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        ctypes.wintypes.HWND(hwnd), ctypes.c_uint(35),
                        ctypes.byref(colorref), ctypes.sizeof(colorref))
            except Exception:
                pass

    else:
        # 明亮主题：清除 QSS，恢复标准调色板
        dialog.setStyleSheet('')
        dialog.setPalette(QApplication.style().standardPalette())
        # Windows DWM 标题栏恢复
        if is_win:
            try:
                import ctypes
                hwnd = int(dialog.winId())
                if hwnd:
                    value = ctypes.c_int(0)
                    for attr in (20, 19):
                        try:
                            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                                ctypes.wintypes.HWND(hwnd), ctypes.c_uint(attr),
                                ctypes.byref(value), ctypes.sizeof(value))
                        except Exception:
                            continue
                    none = ctypes.c_uint(0xFFFFFFFF)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        ctypes.wintypes.HWND(hwnd), ctypes.c_uint(35),
                        ctypes.byref(none), ctypes.sizeof(none))
            except Exception:
                pass

    fit_message_box_buttons(dialog)
    fit_push_button_texts(dialog)
