# Ubuntu 20.04+ 独立打包使用说明

本构建链专门生成可运行于 Ubuntu 20.04 及以上版本的 SerialTool 发布包，不会修改或覆盖现有 Ubuntu 22.04 构建链。

## 构建要求

- 构建系统必须是 Ubuntu 20.04；不能在 Ubuntu 22.04 或更新系统上生成 20.04 兼容包。
- Python 必须为 3.10 或更高版本，并且该解释器本身必须能在 Ubuntu 20.04 上运行。
- Python 需要包含 `venv` 模块。
- `x86_64` 与 `arm64` 需要分别在对应架构上构建。

Ubuntu 20.04 的系统默认 Python 版本低于 3.10。请通过可信来源准备 Python 3.10+；脚本不会替换系统 Python。默认查找 `python3.10`，也可以使用 `SERIALTOOL_PYTHON` 指定完整路径。

## 首次构建

在 Ubuntu 20.04 终端进入项目根目录：

```bash
bash build_ubuntu20.sh --install-system-deps
```

指定其他 Python 3.10+ 解释器：

```bash
SERIALTOOL_PYTHON=/path/to/python3.10 bash build_ubuntu20.sh --install-system-deps
```

脚本会创建独立缓存虚拟环境、按 SHA-256 校验安装锁定依赖，并生成便携版和 `.deb` 安装包。

## 后续构建

```bash
bash build_ubuntu20.sh
```

只生成便携包：

```bash
bash build_ubuntu20.sh --skip-deb
```

指定输出目录：

```bash
bash build_ubuntu20.sh --output-dir /tmp/serialtool-release-2004
```

## 独立目录

- PyInstaller 中间产物：`build/linux20/pyinstaller-dist/SerialTool/`
- 默认发布目录：`dist/linux20/`
- 默认虚拟环境：`~/.cache/serialtool/build-venv-ubuntu20-<Python版本>-<依赖哈希>/`

默认产物：

```text
dist/linux20/SerialTool-<版本>-ubuntu20.04-<架构>.tar.gz
dist/linux20/SerialTool-<版本>-ubuntu20.04-<架构>.deb
dist/linux20/SHA256SUMS
```

这些目录与 Ubuntu 22.04 使用的 `build/linux/`、`dist/linux/` 相互独立。

## 校验和安装

```bash
cd dist/linux20
sha256sum --check SHA256SUMS
sudo apt install ./SerialTool-<版本>-ubuntu20.04-<架构>.deb
serialtool
```

卸载：

```bash
sudo apt remove serialtool
```
