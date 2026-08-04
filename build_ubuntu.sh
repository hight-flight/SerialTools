#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BUILD_VENV="${SERIALTOOL_BUILD_VENV:-${XDG_CACHE_HOME:-${HOME}/.cache}/serialtool/build-venv}"

SYSTEM_PACKAGES=(
  python3-venv
  python3-pip
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
在 Ubuntu 22.04+ 中构建 SerialTool 发布包。

用法：
  ./build_ubuntu.sh [脚本选项] [build_linux.py 参数]

脚本选项：
  --install-system-deps  使用 apt 自动安装系统构建依赖
  -h, --help             显示帮助并退出

常用示例：
  ./build_ubuntu.sh --install-system-deps
  ./build_ubuntu.sh --skip-deb
  ./build_ubuntu.sh --output-dir /tmp/serialtool-release

环境变量：
  SERIALTOOL_BUILD_VENV  自定义构建虚拟环境目录

默认输出：
  dist/linux/SerialTool-<版本>-ubuntu22.04-<架构>.deb
  dist/linux/SerialTool-<版本>-ubuntu22.04-<架构>.tar.gz
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
  echo "错误：必须在 Ubuntu 22.04+ 环境中运行此脚本。" >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "错误：无法识别当前 Linux 发行版。" >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
ubuntu_major="${VERSION_ID%%.*}"
if [[ "${ID:-}" != "ubuntu" || ! "$ubuntu_major" =~ ^[0-9]+$ || "$ubuntu_major" -lt 22 ]]; then
  echo "错误：当前系统不是 Ubuntu 22.04 或更高版本。" >&2
  exit 1
fi

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
    echo "请重新运行：./build_ubuntu.sh --install-system-deps ${builder_args[*]}" >&2
    exit 2
  fi
fi

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  echo "正在创建构建环境：$BUILD_VENV"
  python3 -m venv "$BUILD_VENV"
fi

echo "正在安装 Python 构建依赖……"
PIP_DISABLE_PIP_VERSION_CHECK=1 \
  "$BUILD_VENV/bin/python" -m pip install -r "$SCRIPT_DIR/requirements-build.txt"

echo "开始生成 Ubuntu 发布包……"
cd "$SCRIPT_DIR"
exec "$BUILD_VENV/bin/python" packaging/linux/build_linux.py "${builder_args[@]}"
