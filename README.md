# SerialTools

[![Python](https://img.shields.io/badge/Python-3.8+-blue)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/PyQt5-GUI-orange)](https://pypi.org/project/PyQt5/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> 基于 PyQt5 的强大串口调试工具 / A powerful serial port debugging tool based on PyQt5

---

## ✨ 功能特点

- ✅ **多协议支持** - 串口、UDP、TCP 客户端/TCP 服务器
- ✅ **串口自动检测** - 自动发现并连接可用串口
- ✅ **文本/HEX 双模式** - 支持文本和十六进制数据发送接收
- ✅ **多种校验算法** - Modbus CRC16、CRC32、XOR8、ADD8、ADD16、Fletcher
- ✅ **批量字符串发送** - 预设多条发送指令，快速切换
- ✅ **循环发送** - 支持定时循环发送数据
- ✅ **自动保存日志** - 自动记录通信数据
- ✅ **OTA 固件升级** - 内置 OTA 远程升级中心，支持 HTTP 传输
- ✅ **数据可视化** - 图形化展示通信数据（示波器功能）
- ✅ **串口监视器** - 实时监视串口状态
- ✅ **Modbus 工具** - Modbus 协议调试支持
- ✅ **主题切换** - 支持明暗主题切换
- ✅ **自动回复** - 支持配置规则自动响应接收数据
- ✅ **GSM调试** - 内置 GSM 模块调试功能
- ✅ **JSON数据查看** - 支持 JSON 格式数据解析与可视化

---

## 🚀 快速开始

### 环境要求

- Python 3.8+
- PyQt5
- pyserial

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://gitee.com/hight-flight/SerialTools.git

# 2. 进入目录
cd SerialTools

# 3. 安装运行依赖
pip install -r requirements.txt

# 4. 运行程序
python serial_GUI.py
```

### 依赖文件

- `requirements.txt`：普通源码运行入口，引用 `requirements-runtime.txt`。
- `requirements-build.txt`：开发阶段构建依赖，允许兼容版本范围。
- `requirements-release-windows.txt`：Windows 正式发布锁定依赖及 SHA-256。
- `requirements-release-linux.txt`：Ubuntu 正式发布锁定依赖及 SHA-256。

---

## 📖 使用说明

### 基本操作

1. **连接串口**：选择端口和波特率，点击"打开串口"
2. **发送数据**：在发送区域输入内容，选择Text或HEX模式，点击"发送"
3. **接收数据**：接收区域自动显示收到的数据

### 偏好设置（1.4.5）

通过“设置 → 偏好设置…”或 `Ctrl+,` 打开设置窗口：

- 分别设置接收显示和日志的时间戳格式，提供实时预览。修改只影响后续数据。
- 设置接收字体、字号及长数据自动换行。
- 设置自动跟随、手动查看后的自动恢复与等待时间，以及接收显示行数上限。
- “设置 → 偏好设置 → 窗口布局 → 恢复窗口布局”恢复窗口位置、默认大小和收发区域比例，并收起多字符串面板。

“应用”和“确定”会保存设置；“取消”放弃尚未应用的修改。
“恢复默认”立即应用并保存代码固定的偏好默认值，不会将最近一次修改当作默认值。
窗口布局通过独立的“恢复窗口布局”按钮恢复；通信参数、命令列表和日志文件保留。

| 设置项 | 代码默认值 |
| --- | --- |
| 时间戳显示 | 开启 |
| 显示与日志时间格式 | 日期＋时分秒＋微秒（6 位小数） |
| 接收字体 | Consolas，11pt |
| 长数据自动换行 | 开启 |
| 自动跟随／手动查看后自动恢复 | 开启／开启 |
| 恢复等待时间 | 10 秒 |
| 接收显示行数上限 | 5000 行 |

显示行数上限不改变日志保存范围。手动查看时暂缓清理，超过上限的 3 倍仍会清理旧内容。

### 高级功能

| 功能 | 说明 |
|------|------|
| 批量发送 | 可预设多条指令，按顺序发送；非零顺序号不能重复，0 表示停用且允许重复；开始前标出冲突行并阻止发送 |
| 循环发送 | 设置间隔时间，自动重复发送 |
| 校验计算 | 选择校验算法，自动计算校验码 |
| 日志保存 | 开启自动保存，记录所有通信 |
| OTA 升级 | 启动 HTTP 服务器进行固件远程升级 |
| 示波器 | 图形化显示通信数据变化 |
| 串口监视器 | 实时监视串口引脚状态 |
| Modbus 工具 | Modbus 协议调试 |
| 自动回复 | 配置规则自动响应接收数据 |
| GSM调试 | AT指令调试、短信发送、电话控制 |
| JSON查看器 | 解析并图形化展示 JSON 数据 |

---

## 📁 项目结构

```
SerialTools/
├── serial_GUI.py              # 主程序入口
├── transport.py               # 多协议通信模块（串口/UDP/TCP）
├── dialogs.py                 # 对话框组件
├── auto_reply.py              # 自动回复功能
├── gsm_debugger.py            # GSM 模块调试工具
├── data_viewer.py             # 数据分析面板 / JSON 查看器
├── ota_center.py              # OTA 远程升级中心
├── theme.py                   # 主题管理
├── build_app.py               # 打包脚本
├── serial_config.json         # 配置文件
├── requirements.txt           # 依赖列表
├── themes/                    # 主题配置文件
│   └── design-tokens.md      # 设计系统文档
└── VERSION_INFO_README.md     # 版本信息配置说明
```

---

## 🛠️ 打包发布

默认正式发布包含以下五种产品：

| 平台 | 单文件版 | 便携版 | 安装包 |
|------|------------|--------|--------|
| Windows | `dist/SerialTool.exe` | `dist/SerialTool/` | `dist/SerialTool_Setup.exe` |
| Ubuntu 20.04+ | — | `dist/linux20/SerialTool-<版本>-ubuntu20.04-<架构>.tar.gz` | `dist/linux20/SerialTool-<版本>-ubuntu20.04-<架构>.deb` |
| Ubuntu 22.04+ | — | `dist/linux/SerialTool-<版本>-ubuntu22.04-<架构>.tar.gz` | `dist/linux/SerialTool-<版本>-ubuntu22.04-<架构>.deb` |

Windows 和 Ubuntu 二进制文件必须分别在对应系统中构建，不能跨平台生成。

### Windows

```powershell
python -m pip install --require-hashes -r requirements-release-windows.txt
# 默认同时生成 Windows 单文件版、便携版和安装包
python build_app.py
```

默认模式需要预先安装 Inno Setup 6。按需模式：

```powershell
# 仅生成 Windows 便携版
python build_app.py --onedir

# 仅生成 Windows 单文件程序
python build_app.py --onefile

# 仅生成 Windows 安装包
python build_app.py --setup
```

### Ubuntu 22.04+

Linux 发布包必须在 Ubuntu 22.04 环境中构建：

```bash
chmod +x build_ubuntu.sh
# 默认同时生成 Ubuntu 便携版和 .deb 安装包
./build_ubuntu.sh --install-system-deps
```

脚本会自动创建缓存虚拟环境、按哈希安装锁定依赖并调用 PyInstaller。构建结果位于 `dist/linux/`，同时生成 `.deb`、便携包和 `SHA256SUMS`。完整说明见 [Ubuntu 发布包文档](packaging/linux/README.md)。

### Ubuntu 20.04+

Ubuntu 20.04 兼容包使用独立入口，必须在 Ubuntu 20.04 中通过 Python 3.10+ 构建：

```bash
bash build_ubuntu20.sh --install-system-deps
```

构建结果位于 `dist/linux20/`，不会覆盖 Ubuntu 22.04 产物。完整说明见 [Ubuntu 20.04 独立构建文档](Ubuntu20.04打包使用说明.md)。

---

## 📄 开源协议

本项目基于 [MIT](LICENSE) 协议开源。

---

## 👤 联系与贡献

欢迎提交 Issue 和 Pull Request！

- 作者：GAOXIANG
- 邮箱：770807059@qq.com
