# Ubuntu 22.04+ 打包使用说明

本文说明如何将 SerialTool 打包为可在 Ubuntu 22.04 及以上系统运行的发布包。

## 当前版本与分支

- 软件版本：`1.3.5`
- 开发分支：`feat/ubuntu-packaging`
- 工作目录：`D:\script\code_project\serial_GUI\.worktrees\ubuntu-packaging`
- Ubuntu/WSL 路径：`/mnt/d/script/code_project/serial_GUI/.worktrees/ubuntu-packaging`

## 打包脚本位置

一键打包脚本：

```text
D:\script\code_project\serial_GUI\.worktrees\ubuntu-packaging\build_ubuntu.sh
```

底层 Python 构建程序：

```text
D:\script\code_project\serial_GUI\.worktrees\ubuntu-packaging\packaging\linux\build_linux.py
```

脚本必须在 Ubuntu 22.04 或更高版本的 Linux 环境中运行，不能直接在 Windows PowerShell 中执行。Windows 用户可以使用 WSL2 Ubuntu 22.04。

## 如何打包

首次打包时，进入 Ubuntu 或 WSL 终端：

```bash
cd /mnt/d/script/code_project/serial_GUI/.worktrees/ubuntu-packaging
./build_ubuntu.sh --install-system-deps
```

该命令会安装系统依赖、创建 Python 虚拟环境并生成便携版和 `.deb` 安装包。

后续打包直接运行：

```bash
./build_ubuntu.sh
```

常用参数：

```bash
# 只生成便携版，不生成 .deb 安装包
./build_ubuntu.sh --skip-deb

# 指定发布包输出目录
./build_ubuntu.sh --output-dir /path/to/output

# 查看完整帮助
./build_ubuntu.sh --help
```

如果脚本没有执行权限：

```bash
chmod +x build_ubuntu.sh
```

## 构建结果位置

### PyInstaller 构建文件夹

Windows 路径：

```text
D:\script\code_project\serial_GUI\.worktrees\ubuntu-packaging\build\linux\pyinstaller-dist\SerialTool
```

Linux 主程序：

```text
D:\script\code_project\serial_GUI\.worktrees\ubuntu-packaging\build\linux\pyinstaller-dist\SerialTool\SerialTool
```

### 便携版

```text
D:\script\code_project\serial_GUI\.worktrees\ubuntu-packaging\dist\linux\SerialTool-1.3.5-ubuntu22.04-x86_64.tar.gz
```

### Ubuntu 安装包

```text
D:\script\code_project\serial_GUI\.worktrees\ubuntu-packaging\dist\linux\SerialTool-1.3.5-ubuntu22.04-x86_64.deb
```

默认文件名格式：

```text
dist/linux/SerialTool-<版本>-ubuntu22.04-<架构>.tar.gz
dist/linux/SerialTool-<版本>-ubuntu22.04-<架构>.deb
```

## 使用便携版

在 Ubuntu 中执行：

```bash
tar -xzf dist/linux/SerialTool-1.3.5-ubuntu22.04-x86_64.tar.gz
cd SerialTool-1.3.5-ubuntu22.04-x86_64
./SerialTool
```

便携版无需安装，但目标系统仍需具备桌面图形环境以及程序运行所需的基础系统库。

## 安装和卸载 `.deb` 包

安装：

```bash
sudo apt install ./dist/linux/SerialTool-1.3.5-ubuntu22.04-x86_64.deb
```

安装完成后运行：

```bash
serialtool
```

卸载：

```bash
sudo apt remove serialtool
```

## 串口权限

如果程序无法打开 `/dev/ttyUSB*` 或 `/dev/ttyACM*` 串口，将当前用户加入 `dialout` 用户组：

```bash
sudo usermod -aG dialout "$USER"
```

执行后注销并重新登录，使权限生效。

## 用户数据位置

- 配置：`~/.config/SerialTool/`
- 日志和 OTA 数据：`~/.local/share/SerialTool/`
- 缓存：`~/.cache/SerialTool/`

## 架构说明

当前已有构建产物为 `x86_64`。项目也支持生成 `arm64` 包，但应在对应架构的 Ubuntu 22.04+ 环境中原生构建；不要直接将 `x86_64` 包复制到 ARM 设备运行。

## 验证状态

- 一键打包脚本已在 Ubuntu 22.04 WSL 环境实际执行成功。
- 当前目录已生成 `x86_64` 便携版和 `.deb` 安装包。
- Linux 自动化测试已通过。

更详细的构建原理和排错说明见：`packaging/linux/README.md`。
