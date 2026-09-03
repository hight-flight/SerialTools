# 全界面 UI 优化实现计划

> **给 agent 执行者：** 必需子 skill：使用 superpowers:executing-plans，逐任务实现本计划。步骤使用复选框（`- [ ]`）语法进行跟踪。

**目标：** 修复主窗口、工具子窗口和数据分析面板中已确认的布局、主题、状态反馈和可访问性问题，同时保持现有业务功能。

**架构：** 先在 `theme.py` 建立复用的主题和屏幕尺寸辅助能力，再分别收敛主窗口、小工具窗口及数据分析模块。所有行为变化先加入回归测试；加密的 `serial_GUI.py` 继续通过本地 Python 解密视图进行精确替换，其余文件使用补丁修改。

**技术栈：** Python 3.12、PyQt5、unittest、pyqtgraph、numpy。

---

### 任务 1：主题基础与窗口生命周期

**文件：**
- 修改：`theme.py`
- 修改：`serial_GUI.py`
- 测试：`tests/test_ui_regressions.py`

- [x] **步骤 1：编写失败测试**

```python
def test勾选框显示明确勾号且子窗口支持主题往返(self):
    self.assertIn("QCheckBox::indicator:checked", DARK_QSS)
    self.assertIn("_refresh_open_dialog_themes", inspect.getsource(SerialTool.apply_theme))
```

- [x] **步骤 2：运行测试，确认因缺少勾号资源和子窗口刷新机制而失败**

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_ui_regressions
```

- [x] **步骤 3：实现最小修复**

```python
def register_child_dialog(self, dialog):
    self._open_theme_dialogs.add(dialog)
    dialog.destroyed.connect(lambda: self._open_theme_dialogs.discard(dialog))
```

同时为亮暗主题提供清晰的选中勾号，并让 `apply_theme()` 刷新所有仍存活的子窗口和原生标题栏。

- [x] **步骤 4：运行测试并确认通过**

### 任务 2：主窗口响应式布局与多字符串区域

**文件：**
- 修改：`serial_GUI.py`
- 测试：`tests/test_ui_regressions.py`

- [x] **步骤 1：编写失败测试**

```python
def test主窗口按工作区限制尺寸并为完整路径提供提示(self):
    source = inspect.getsource(SerialTool.init_ui)
    self.assertIn("setToolTip(self.save_directory)", source)
    self.assertNotIn("setFixedHeight(26)", source)
```

- [x] **步骤 2：运行测试，确认当前固定高度和路径提示缺失导致失败**
- [x] **步骤 3：用最小高度替代固定高度；为路径提供 tooltip 和光标起始位置；按两侧最小尺寸计算展开宽度；为状态栏长文本提供省略与 tooltip**
- [x] **步骤 4：运行主界面和多字符串回归测试**

### 任务 3：示波器窗口状态与空状态

**文件：**
- 修改：`serial_GUI.py`
- 测试：`tests/test_ui_regressions.py`

- [x] **步骤 1：编写失败测试**

```python
def test示波器只保留一个窗口且空状态不会被清除(self):
    source = inspect.getsource(SerialTool.oscilloscope)
    self.assertIn("_scope_dialog", source)
    self.assertIn("plot_widget.addItem(empty_text)", source)
```

- [x] **步骤 2：运行测试并确认失败**
- [x] **步骤 3：将示波器做成可激活的单例窗口；状态和当前值标签改为窗口局部状态；重建曲线后重新加入空提示；亮暗主题使用不同曲线调色板**
- [x] **步骤 4：运行测试并确认通过**

### 任务 4：通用工具子窗口

**文件：**
- 修改：`dialogs.py`
- 修改：`gsm_debugger.py`
- 修改：`ota_center.py`
- 修改：`auto_reply.py`
- 测试：`tests/test_ui_regressions.py`
- 测试：`tests/test_gsm_debugger.py`

- [x] **步骤 1：编写失败测试，覆盖 HEX Decimal 输入、紧凑窗口、表格列策略和语义颜色**
- [x] **步骤 2：运行测试并确认相应行为缺失**
- [x] **步骤 3：实现 Decimal 双向转换；压缩 CRC/HEX 空白；串口/GSM 表格按字段类型分配列宽并补 tooltip；按当前屏幕限制窗口尺寸；OTA/自动应答使用主题 token**
- [x] **步骤 4：运行相关测试并确认通过**

### 任务 5：数据分析核心故障

**文件：**
- 修改：`data_viewer.py`
- 测试：`tests/test_data_viewer.py`

- [x] **步骤 1：编写失败测试**

```python
def test缺少图表依赖时统计面板仍能构造(self):
    tracker = ChartTrackerWidget.__new__(ChartTrackerWidget)
    tracker._tracked = []
    self.assertEqual(tracker._tracked, [])
```

并覆盖颜色菜单、别名同步、计算字段持久化、主题高亮刷新和快捷键焦点判断。

- [x] **步骤 2：运行测试并确认失败**
- [x] **步骤 3：在依赖检查前初始化公共状态；保存 chip 颜色控件；通过信号统一别名/颜色状态；只持久化语义别名；主题切换调用 `rehighlight()`；快捷键仅在允许的上下文触发**
- [x] **步骤 4：运行数据分析测试并确认通过**

### 任务 6：数据分析布局、反馈和可访问性

**文件：**
- 修改：`data_viewer.py`
- 测试：`tests/test_data_viewer.py`
- 测试：`tests/test_ui_regressions.py`

- [x] **步骤 1：编写失败测试覆盖亮色调色板、工具栏滚动、统计浮层边界、数据表采样列、空状态、Splitter 恢复、字段验证、完整值 tooltip 和导出反馈**
- [x] **步骤 2：运行测试并确认失败**
- [x] **步骤 3：实现主题调色板和重绘；字段栏可横向滚动；限制统计浮层；增加采样列和表头 tooltip；补齐空状态与按钮禁用；保存非零 Splitter 比例；使用编辑器验证；为截断值保留全文；处理保存异常和成功反馈；限制 HEX 预览高度**
- [x] **步骤 4：补齐 buddy、accessibleName 和键盘操作，再运行测试**

### 任务 7：完整验证

**文件：**
- 验证：全部相关源文件和测试

- [x] **步骤 1：运行完整测试套件**

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [x] **步骤 2：启动应用，检查亮色/暗色、主窗口、多字符串和所有子窗口在正常尺寸下的显示**
- [x] **步骤 3：检查 720×560、小高度工作区及高 DPI 风险路径，不保留测试产生的临时窗口**
