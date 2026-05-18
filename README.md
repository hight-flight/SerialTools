# TRAE 串口调试助手 / TRAE Serial Debug Tool

![Python](https://img.shields.io/badge/Python-3.8+-blue)
![PyQt5](https://img.shields.io/badge/PyQt5-GUI-orange)
![License](https://img.shields.io/badge/License-MIT-green)

> A powerful serial port debugging tool based on PyQt5 /
> 基于 PyQt5 的强大串口调试工具

---

## ✨ 功能特点 / Features

- ✅ 串口自动检测与连接 / Auto-detect and connect serial ports
- ✅ 文本/HEX 双模式发送接收 / Text/HEX send and receive
- ✅ 多种校验算法支持 / Multiple checksum algorithms
  - Modbus CRC16 / CRC32 / XOR8 / ADD8 / ADD16 / Fletcher
- ✅ 批量字符串发送 / Batch string sending
- ✅ 循环发送 / Cyclic sending
- ✅ 自动保存日志 / Auto-save logs
- ✅ 数据可视化展示 / Data visualization

---

## 🚀 快速开始 / Quick Start

### 安装 / Installation

```bash
# 1. 克隆仓库 / Clone repository
git clone https://gitee.com/hight-flight/trae_project.git

# 2. 安装依赖 / Install dependencies
cd trae_project
pip install PyQt5 pyserial

# 3. 运行程序 / Run the program
python serial_GUI.py