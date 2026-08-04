# Ubuntu 发布包

Linux 产物必须在目标架构的 Ubuntu 22.04 环境中构建。以最低支持版本构建，可以避免在较新系统构建后因 glibc 版本过高而无法运行。

## 构建环境

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip dpkg-dev \
  libgl1 libxkbcommon-x11-0 libxcb-xinerama0 libxcb-keysyms1 \
  libxcb-shape0 libxcb-icccm4 libxcb-cursor0 fonts-noto-cjk

python3 -m venv .venv-linux
source .venv-linux/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
```

## 生成发布包

```bash
python packaging/linux/build_linux.py
```

输出目录为 `dist/linux/`，包含：

- `SerialTool-<版本>-ubuntu22.04-<架构>.deb`：Ubuntu 安装包。
- `SerialTool-<版本>-ubuntu22.04-<架构>.tar.gz`：无需安装的便携目录包。

构建机架构为 `x86_64` 时生成 `amd64` 安装包；在 `aarch64` 构建机上生成 `arm64` 安装包。PyInstaller 不支持跨架构生成原生程序。

## 安装和串口权限

```bash
sudo apt install ./dist/linux/SerialTool-*-ubuntu22.04-*.deb
sudo usermod -aG dialout "$USER"
```

加入 `dialout` 组后需要注销并重新登录。安装完成后可从应用菜单启动 SerialTool，也可以执行：

```bash
serialtool
```

用户数据遵循 XDG 目录规范：

- 配置：`~/.config/SerialTool/`
- 日志和 OTA 文件：`~/.local/share/SerialTool/`
- 运行时缓存：`~/.cache/SerialTool/`

## 卸载

```bash
sudo apt remove serialtool
```
