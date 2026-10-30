"""应用偏好及固定默认值；默认值沿用 1.4.4 的代码行为。"""

import datetime
import re
from dataclasses import asdict, dataclass, fields

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFontComboBox,
    QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)


TIMESTAMP_FORMATS = (
    ('time', '时分秒'),
    ('time_ms', '时分秒＋毫秒'),
    ('date_ms', '日期＋时分秒＋毫秒'),
    ('date_us', '日期＋时分秒＋微秒（原默认）'),
)
_STAMP_PATTERN = re.compile(r'^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\.(\d{6})\] $')


@dataclass(frozen=True)
class Preferences:
    show_timestamp: bool = True
    display_timestamp: str = 'date_us'
    log_timestamp: str = 'date_us'
    receive_font: str = 'Consolas'
    receive_font_size: int = 11
    word_wrap: bool = True
    auto_scroll: bool = True
    auto_resume: bool = True
    resume_delay_seconds: int = 10
    max_display_lines: int = 5000

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """只接收已知且有效的字段，兼容旧配置和损坏的配置值。"""
        defaults = cls()
        if not isinstance(data, dict):
            return defaults
        values = defaults.to_dict()
        limits = {'receive_font_size': (8, 32), 'resume_delay_seconds': (1, 120),
                  'max_display_lines': (500, 100000)}
        formats = {key for key, _ in TIMESTAMP_FORMATS}
        for field in fields(cls):
            key = field.name
            value = data.get(key)
            if isinstance(values[key], bool):
                valid = isinstance(value, bool)
            elif key in limits:
                low, high = limits[key]
                valid = type(value) is int and low <= value <= high
            elif key.endswith('timestamp'):
                valid = isinstance(value, str) and value in formats
            else:
                valid = isinstance(value, str) and 0 < len(value.strip()) <= 128 and not any(
                    ord(char) < 32 for char in value)
            if valid:
                values[key] = value
        return cls(**values)


# 固定基准，不能由用户配置或“应用”操作覆盖。
DEFAULT_PREFERENCES = Preferences()


def format_timestamp(timestamp, kind):
    """转换同一次采集的时间戳；保留未知格式，避免改写外部文本。"""
    if kind == 'date_us':
        return timestamp
    match = _STAMP_PATTERN.fullmatch(timestamp)
    if not match:
        return timestamp
    date, clock, fraction = match.groups()
    if kind == 'time':
        return f'[{clock}] '
    if kind == 'time_ms':
        return f'[{clock}.{fraction[:3]}] '
    if kind == 'date_ms':
        return f'[{date} {clock}.{fraction[:3]}] '
    return timestamp


def receive_font_stylesheet(preferences):
    family = preferences.receive_font.replace('\\', '\\\\').replace('"', '\\"')
    return f'QTextEdit {{ font-family: "{family}", "Microsoft YaHei"; font-size: {preferences.receive_font_size}pt; }}'


class PreferencesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.tool = parent
        self.setWindowTitle('偏好设置')
        self.resize(620, 540)
        screen = parent.screen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(620, available.width()), min(540, available.height()))
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        display = self._page('显示与时间戳')
        form = QFormLayout()
        form.setSpacing(10)
        self.show_timestamp = QCheckBox('接收区显示时间戳')
        form.addRow(self.show_timestamp)
        self.display_format = self._timestamp_combo()
        self.log_format = self._timestamp_combo()
        form.addRow('显示时间格式', self.display_format)
        form.addRow('日志时间格式', self.log_format)
        self.timestamp_preview = QLabel()
        self.timestamp_preview.setWordWrap(True)
        form.addRow('实时预览', self.timestamp_preview)
        self.font_family = QFontComboBox()
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 32)
        self.font_size.setSuffix(' pt')
        font_row = QHBoxLayout()
        font_row.addWidget(self.font_family, 1)
        font_row.addWidget(self.font_size)
        form.addRow('接收字体', font_row)
        self.word_wrap = QCheckBox('长数据自动换行')
        form.addRow(self.word_wrap)
        display.addLayout(form)
        self.font_preview = QTextEdit()
        self.font_preview.setReadOnly(True)
        self.font_preview.setMinimumHeight(80)
        self.font_preview.setMaximumHeight(100)
        self.font_preview.setPlainText('接收数据预览：设备运行正常\nRX 01 03 00 00 00 02 C4 0B')
        display.addWidget(self.font_preview)
        note = QLabel('时间格式应用于后续数据，已有内容保持原样。日志时间格式独立于界面显示。')
        note.setWordWrap(True)
        display.addWidget(note)
        display.addStretch()

        behavior = self._page('接收行为')
        behavior_form = QFormLayout()
        behavior_form.setSpacing(12)
        self.auto_scroll = QCheckBox('自动跟随最新数据')
        self.auto_resume = QCheckBox('手动查看后自动恢复跟随')
        self.resume_delay = QSpinBox()
        self.resume_delay.setRange(1, 120)
        self.resume_delay.setSuffix(' 秒')
        self.max_lines = QSpinBox()
        self.max_lines.setRange(500, 100000)
        self.max_lines.setSingleStep(500)
        self.max_lines.setSuffix(' 行')
        behavior_form.addRow(self.auto_scroll)
        behavior_form.addRow(self.auto_resume)
        behavior_form.addRow('停止滚动后等待', self.resume_delay)
        behavior_form.addRow('接收显示行数上限', self.max_lines)
        behavior.addLayout(behavior_form)
        note = QLabel('超过上限时移除较早的显示内容，日志记录不受影响。\n'
                      '手动查看时暂缓清理，超过上限的 3 倍后仍会清理，以限制内存占用。')
        note.setWordWrap(True)
        behavior.addWidget(note)
        behavior.addStretch()

        layout_page = self._page('窗口布局')
        note = QLabel('恢复窗口默认大小和屏幕内位置、接收与发送区比例，并收起多字符串发送面板。\n'
                      '恢复后立即保存，通信连接和收发内容保持不变。')
        note.setWordWrap(True)
        layout_page.addWidget(note)
        self.restore_layout_button = QPushButton('恢复窗口布局')
        self.restore_layout_button.clicked.connect(self._restore_layout)
        layout_page.addWidget(self.restore_layout_button, alignment=Qt.AlignLeft)
        layout_page.addStretch()

        self.result_label = QLabel('恢复默认会立即应用并保存本窗口的全部偏好；窗口布局可单独恢复。')
        self.result_label.setWordWrap(True)
        root.addWidget(self.result_label)
        footer = QHBoxLayout()
        self.reset_button = QPushButton('恢复默认')
        self.reset_button.clicked.connect(self.restore_defaults)
        footer.addWidget(self.reset_button)
        footer.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply)
        self.buttons.button(QDialogButtonBox.Ok).setText('确定')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.button(QDialogButtonBox.Apply).setText('应用')
        self.buttons.accepted.connect(self._accept_changes)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply_changes)
        footer.addWidget(self.buttons)
        root.addLayout(footer)
        self.display_format.currentIndexChanged.connect(self._update_preview)
        self.log_format.currentIndexChanged.connect(self._update_preview)
        self.font_family.currentFontChanged.connect(self._update_preview)
        self.font_size.valueChanged.connect(self._update_preview)
        self.word_wrap.toggled.connect(self._update_preview)
        self.auto_scroll.toggled.connect(self._update_enabled)
        self.auto_resume.toggled.connect(self._update_enabled)
        self._load_values(parent.current_preferences())
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(1000)
        self.preview_timer.timeout.connect(self._update_preview)
        self.preview_timer.start()

    def _page(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        return layout

    @staticmethod
    def _timestamp_combo():
        combo = QComboBox()
        for key, text in TIMESTAMP_FORMATS:
            combo.addItem(text, key)
        return combo

    def _load_values(self, prefs):
        self.show_timestamp.setChecked(prefs.show_timestamp)
        self.display_format.setCurrentIndex(self.display_format.findData(prefs.display_timestamp))
        self.log_format.setCurrentIndex(self.log_format.findData(prefs.log_timestamp))
        self.font_family.setCurrentFont(QFont(prefs.receive_font))
        self.font_size.setValue(prefs.receive_font_size)
        self.word_wrap.setChecked(prefs.word_wrap)
        self.auto_scroll.setChecked(prefs.auto_scroll)
        self.auto_resume.setChecked(prefs.auto_resume)
        self.resume_delay.setValue(prefs.resume_delay_seconds)
        self.max_lines.setValue(prefs.max_display_lines)
        self._update_enabled()
        self._update_preview()

    def values(self):
        return Preferences(
            show_timestamp=self.show_timestamp.isChecked(),
            display_timestamp=self.display_format.currentData(),
            log_timestamp=self.log_format.currentData(),
            receive_font=self.font_family.currentFont().family(),
            receive_font_size=self.font_size.value(),
            word_wrap=self.word_wrap.isChecked(),
            auto_scroll=self.auto_scroll.isChecked(),
            auto_resume=self.auto_resume.isChecked(),
            resume_delay_seconds=self.resume_delay.value(),
            max_display_lines=self.max_lines.value(),
        )

    def _update_enabled(self, *_args):
        enabled = self.auto_scroll.isChecked()
        self.auto_resume.setEnabled(enabled)
        self.resume_delay.setEnabled(enabled and self.auto_resume.isChecked())

    def _update_preview(self, *_args):
        stamp = datetime.datetime.now().strftime('[%Y-%m-%d %H:%M:%S.%f] ')
        self.timestamp_preview.setText(
            '显示：' + format_timestamp(stamp, self.display_format.currentData()) + '\n'
            '日志：' + format_timestamp(stamp, self.log_format.currentData()))
        self.font_preview.setStyleSheet(receive_font_stylesheet(self.values()))
        self.font_preview.setLineWrapMode(QTextEdit.WidgetWidth if self.word_wrap.isChecked() else QTextEdit.NoWrap)

    def apply_changes(self):
        if not self.tool.apply_preferences(self.values()):
            QMessageBox.warning(self, '保存失败', '设置未能完整保存，请检查配置目录是否可写。')
            return False
        self.result_label.setText('设置已应用并保存。')
        return True

    def _accept_changes(self):
        if self.apply_changes():
            self.accept()

    def restore_defaults(self):
        self._load_values(DEFAULT_PREFERENCES)
        if self.tool.apply_preferences(DEFAULT_PREFERENCES):
            self.result_label.setText('已恢复并保存代码默认设置。')
        else:
            QMessageBox.warning(self, '保存失败', '默认设置已应用，但未能完整保存。')

    def _restore_layout(self):
        if self.tool.restore_default_layout():
            self.result_label.setText('窗口布局已恢复并保存。')
        else:
            QMessageBox.warning(self, '保存失败', '布局已恢复，但未能完整保存。')

    def done(self, result):
        self.preview_timer.stop()
        super().done(result)
