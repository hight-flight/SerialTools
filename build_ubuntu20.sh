#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OS_RELEASE_FILE="${SERIALTOOL_OS_RELEASE_FILE:-/etc/os-release}"
LOCK_FILE="$SCRIPT_DIR/requirements-release-linux.txt"
LOCK_HASH="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
PYTHON_BIN="${SERIALTOOL_PYTHON:-python3.10}"

SYSTEM_PACKAGES=(
  binutils
  dpkg-dev
  libgl1
  libxkbcommon-x11-0
  libxcb-xinerama0
  libxcb-keysyms1
  libxcb-shape0
  libxcb-icccm4
  libxcb-cursor0
  fonts-noto-cjk
)

show_help() {
  cat <<'EOF'
在 Ubuntu 20.04 中构建可用于 Ubuntu 20.04+ 的 SerialTool 独立发布包。

用法：
  ./build_ubuntu20.sh [脚本选项] [build_linux_2004.py 参数]

脚本选项：
  --install-system-deps  使用 apt 自动安装系统构建依赖
  -h, --help             显示帮助并退出

常用示例：
  ./build_ubuntu20.sh --install-system-deps
  ./build_ubuntu20.sh --skip-deb
  ./build_ubuntu20.sh --output-dir /tmp/serialtool-release-2004

环境变量：
  SERIALTOOL_PYTHON      Python 3.10+ 解释器，默认 python3.10
  SERIALTOOL20_BUILD_VENV  自定义 Ubuntu 20.04 构建虚拟环境目录

默认输出：
  dist/linux20/SerialTool-<版本>-ubuntu20.04-<架构>.deb
  dist/linux20/SerialTool-<版本>-ubuntu20.04-<架构>.tar.gz
EOF
}

install_system_deps=false
builder_args=()
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      show_help
      exit 0
      ;;
    --install-system-deps)
      install_system_deps=true
      ;;
    *)
      builder_args+=("$arg")
      ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "错误：必须在 Ubuntu 20.04 环境中运行此脚本。" >&2
  exit 1
fi

if [[ ! -r "$OS_RELEASE_FILE" ]]; then
  echo "错误：无法识别当前 Linux 发行版。" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$OS_RELEASE_FILE"
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "20.04" ]]; then
  echo "错误：兼容 Ubuntu 20.04+ 的正式发布包必须在 Ubuntu 20.04 中构建。" >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "错误：找不到 Python 3.10+ 解释器：$PYTHON_BIN" >&2
  echo "请安装可在 Ubuntu 20.04 运行的 Python 3.10+，或设置 SERIALTOOL_PYTHON。" >&2
  exit 3
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "错误：构建依赖要求 Python 3.10+，当前解释器为：$($PYTHON_BIN --version 2>&1)" >&2
  exit 3
fi

PYTHON_TAG="$($PYTHON_BIN -c 'import sys; print(f"py{sys.version_info.major}{sys.version_info.minor}")')"
DEFAULT_BUILD_VENV="${XDG_CACHE_HOME:-${HOME}/.cache}/serialtool/build-venv-ubuntu20-${PYTHON_TAG}-${LOCK_HASH:0:12}"
BUILD_VENV="${SERIALTOOL20_BUILD_VENV:-$DEFAULT_BUILD_VENV}"

missing_packages=()
for package_name in "${SYSTEM_PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package_name" 2>/dev/null | grep -q "install ok installed"; then
    missing_packages+=("$package_name")
  fi
done

if (( ${#missing_packages[@]} > 0 )); then
  if [[ "$install_system_deps" == true ]]; then
    echo "正在安装系统依赖：${missing_packages[*]}"
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing_packages[@]}"
  else
    echo "缺少系统依赖：${missing_packages[*]}" >&2
    echo "请重新运行：./build_ubuntu20.sh --install-system-deps ${builder_args[*]}" >&2
    exit 2
  fi
fi

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  echo "正在创建 Ubuntu 20.04 构建环境：$BUILD_VENV"
  if ! "$PYTHON_BIN" -m venv "$BUILD_VENV"; then
    echo "错误：无法创建虚拟环境，请确认该 Python 已安装 venv 模块。" >&2
    exit 3
  fi
fi

echo "正在安装 Python 构建依赖……"
PIP_DISABLE_PIP_VERSION_CHECK=1 \
  "$BUILD_VENV/bin/python" -m pip install \
    --require-hashes \
    -r "$LOCK_FILE"

echo "开始生成 Ubuntu 20.04+ 发布包……"
cd "$SCRIPT_DIR"
"$BUILD_VENV/bin/python" packaging/linux/build_linux_2004.py "${builder_args[@]}"
echo "Ubuntu 20.04+ 发布包构建完成。"
