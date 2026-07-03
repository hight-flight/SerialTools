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

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行程序
python serial_GUI.py
```

### 依赖文件 (requirements.txt)

```
PyQt5
pyserial
```

---

## 📖 使用说明

### 基本操作

1. **连接串口**：选择端口和波特率，点击"打开串口"
2. **发送数据**：在发送区域输入内容，选择Text或HEX模式，点击"发送"
3. **接收数据**：接收区域自动显示收到的数据

### 高级功能

| 功能 | 说明 |
|------|------|
| 批量发送 | 可预设多条指令，按需发送 |
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

如需打包为可执行文件：

```bash
python build_app.py
```

---

## 📄 开源协议

本项目基于 [MIT](LICENSE) 协议开源。

---

## 👤 联系与贡献

欢迎提交 Issue 和 Pull Request！

- 作者：GAOXIANG
- 邮箱：770807059@qq.com
