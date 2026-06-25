"""
主题模块：颜色常量、QSS 样式表、对话框主题应用工具函数。
"""

VERSION = "1.3.2"

import platform

from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

# --- 主题颜色常量 ---
THEME_COLORS = {
    'light': {
        'text_normal':    QColor(0, 0, 0),
        'text_send':      QColor(0, 0, 255),
        'text_system':    QColor(128, 128, 128),
        'text_error':     QColor(255, 0, 0),
        'highlight_bg':   QColor(255, 235, 60, 140),  # 明亮模式高亮背景（半透明黄）
        'ansi_default_fg': QColor(0, 0, 0),
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
        'text_normal':    QColor(0xAB, 0xB2, 0xBF),
        'text_send':      QColor(0x61, 0xAF, 0xEF),
        'text_system':    QColor(0x5C, 0x63, 0x70),
        'text_error':     QColor(0xE0, 0x6C, 0x75),
        'highlight_bg':   QColor(200, 160, 0, 100),   # 暗黑模式高亮背景（半透明暖金）
        'ansi_default_fg': QColor(0xAB, 0xB2, 0xBF),
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
QComboBox {
    background-color: #2C313C; color: #ABB2BF;
    border: 1px solid #3E4451; border-radius: 4px; padding: 4px 8px;
}
QComboBox:hover { border-color: #528BFF; }
QComboBox:focus { border-color: #528BFF; }
QComboBox:disabled { background-color: #21252B; color: #7A8294; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(__ARROW_DARK__); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background-color: #21252B; color: #ABB2BF;
    selection-background-color: #3E4451; border: 1px solid #181A1F;
    outline: none;
}
QPushButton {
    background-color: #2C313C; color: #ABB2BF;
    border: 1px solid #3E4451; border-radius: 4px; padding: 4px 12px;
}
QPushButton:hover    { background-color: #3E4451; }
QPushButton:pressed  { background-color: #21252B; }
QPushButton:checked  { background-color: #528BFF; color: #FFFFFF; }
QPushButton:checked:hover { background-color: #61AFEF; }
QPushButton:disabled { background-color: #21252B; color: #7A8294; }
QCheckBox   { color: #ABB2BF; spacing: 6px; }
QCheckBox::indicator {
    background-color: #2C313C; border: 1px solid #3E4451;
    border-radius: 3px; width: 16px; height: 16px;
}
QCheckBox::indicator:hover { border-color: #528BFF; }
QCheckBox::indicator:checked { background-color: #528BFF; border-color: #528BFF; }
QCheckBox::indicator:disabled { background-color: #21252B; border-color: #2C313C; }
QLabel      { color: #ABB2BF; }
QSpinBox {
    background-color: #2C313C; color: #ABB2BF;
    border: 1px solid #3E4451; border-radius: 4px; padding: 4px 4px;
}
QSpinBox:focus { border-color: #528BFF; }
QSpinBox:disabled { background-color: #21252B; color: #7A8294; }
QTableView, QTableWidget {
    background-color: #2C313C; color: #ABB2BF;
    border: 1px solid #3E4451; gridline-color: #3E4451;
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
    background-color: #21252B; color: #ABB2BF;
    border: 1px solid #3E4451; padding: 4px;
}
QHeaderView::section:hover { background-color: #2C313C; }
QStatusBar  { background-color: #21252B; border-top: 1px solid #181A1F; color: #6A7384; }
QStatusBar QLabel#status_sep { color: #3E4451; }
QSplitter::handle { background-color: #3E4451; }
QSplitter::handle:hover { background-color: #4B5363; }
QSplitter::handle:pressed { background-color: #5C6370; }
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
QPushButton:disabled { background-color: #F0F0F0; color: #767676; }
QCheckBox   { color: #333333; spacing: 6px; }
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
QStatusBar  { background-color: #E8E8E8; border-top: 1px solid #CCCCCC; color: #333333; }
QStatusBar QLabel#status_sep { color: #CCCCCC; }
QSplitter::handle { background-color: #CCCCCC; }
QSplitter::handle:hover { background-color: #AAAAAA; }
QSplitter::handle:pressed { background-color: #999999; }
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


def apply_dialog_theme(dialog, is_dark):
    """为子对话框应用当前主题（暗黑/明亮），支持运行时切换。"""
    is_win = platform.system() == 'Windows'

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
                    r, g, b = 0x2C, 0x31, 0x3C
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
