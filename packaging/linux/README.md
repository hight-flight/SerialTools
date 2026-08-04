# Ubuntu 发布包

默认运行会同时生成 Ubuntu 便携版和 `.deb` 安装包；与 Windows 中默认运行 `python build_app.py` 生成的单文件版、便携版和安装包共同构成五种正式发布产品。

Linux 产物必须在目标架构的 Ubuntu 22.04 环境中构建。脚本会拒绝 Ubuntu 24.04 等其他构建系统，防止生成依赖更高版本 glibc 却被错误标记为兼容 22.04 的发布包。

## 构建环境

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip dpkg-dev \
  libgl1 libxkbcommon-x11-0 libxcb-xinerama0 libxcb-keysyms1 \
  libxcb-shape0 libxcb-icccm4 libxcb-cursor0 fonts-noto-cjk

python3 -m venv .venv-linux
source .venv-linux/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements-release-linux.txt
```

## 生成发布包

推荐直接使用一键脚本。首次运行可以自动安装系统依赖：

```bash
chmod +x build_ubuntu.sh
./build_ubuntu.sh --install-system-deps
```

后续构建直接执行：

```bash
./build_ubuntu.sh
```

也可以手动进入已经准备好的 Python 环境后运行底层构建器：

```bash
python packaging/linux/build_linux.py
```

输出目录为 `dist/linux/`，包含：

- `SerialTool-<版本>-ubuntu22.04-<架构>.deb`：Ubuntu 安装包。
- `SerialTool-<版本>-ubuntu22.04-<架构>.tar.gz`：无需安装的便携目录包。
- `SHA256SUMS`：以上发布包的 SHA-256 校验文件。

构建机架构为 `x86_64` 时生成 `amd64` 安装包；在 `aarch64` 构建机上生成 `arm64` 安装包。PyInstaller 不支持跨架构生成原生程序。

校验下载或复制后的发布包：

```bash
cd dist/linux
sha256sum --check SHA256SUMS
```

构建时间默认取当前 Git 提交时间，也可以通过 `SOURCE_DATE_EPOCH` 显式指定，以获得稳定的归档元数据。

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
