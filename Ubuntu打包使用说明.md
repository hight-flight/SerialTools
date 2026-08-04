# Ubuntu 22.04+ 打包使用说明

本文说明如何在项目根目录生成可运行于 Ubuntu 22.04 及以上系统的 SerialTool 发布包。

## 构建要求

- 正式兼容包必须在 Ubuntu 22.04 中构建，不能在 Ubuntu 24.04 上构建后标记为 22.04 包。
- `x86_64` 和 `arm64` 必须分别在对应架构的原生系统中构建。
- Windows 用户可以使用安装了 Ubuntu 22.04 的 WSL2。

## 脚本位置

- 一键脚本：`build_ubuntu.sh`
- 底层构建器：`packaging/linux/build_linux.py`
- Linux 发布锁：`requirements-release-linux.txt`

## 首次打包

在 Ubuntu 22.04 终端进入项目根目录：

```bash
chmod +x build_ubuntu.sh
./build_ubuntu.sh --install-system-deps
```

脚本会安装系统依赖、创建缓存虚拟环境、按 SHA-256 验证并安装锁定的 Python 依赖，然后生成便携版和 `.deb` 安装包。

项目的默认正式发布产品为：Windows 便携版、Windows 安装包、Ubuntu 便携版和 Ubuntu 安装包。Windows 产品使用 `python build_app.py` 在 Windows 中构建；本脚本默认生成两种 Ubuntu 产品。

## 后续打包

```bash
./build_ubuntu.sh
```

常用参数：

```bash
# 只生成便携包
./build_ubuntu.sh --skip-deb

# 指定发布目录，不能放在 build/linux 内
./build_ubuntu.sh --output-dir /path/to/output

./build_ubuntu.sh --help
```

## 输出位置

PyInstaller 中间产物：

```text
build/linux/pyinstaller-dist/SerialTool/
```

默认发布产物：

```text
dist/linux/SerialTool-<版本>-ubuntu22.04-<架构>.tar.gz
dist/linux/SerialTool-<版本>-ubuntu22.04-<架构>.deb
dist/linux/SHA256SUMS
```

校验发布包：

```bash
cd dist/linux
sha256sum --check SHA256SUMS
```

## 使用便携版

```bash
tar -xzf SerialTool-<版本>-ubuntu22.04-<架构>.tar.gz
cd SerialTool-<版本>-ubuntu22.04-<架构>
./SerialTool
```

## 安装和卸载

```bash
sudo apt install ./SerialTool-<版本>-ubuntu22.04-<架构>.deb
serialtool
sudo apt remove serialtool
```

## 串口权限

```bash
sudo usermod -aG dialout "$USER"
```

执行后注销并重新登录。

## 用户数据位置

- 配置：`~/.config/SerialTool/`
- 日志和 OTA 数据：`~/.local/share/SerialTool/`
- 缓存：`~/.cache/SerialTool/`

OTA HTTP 服务只允许访问当前选中的固件文件，但服务启动期间仍会监听局域网接口；只应在可信网络中启用。

更多构建细节见 `packaging/linux/README.md`。
