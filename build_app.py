#!/usr/bin/env python3
"""
串口调试助手 - 打包脚本
使用PyInstaller API进行打包
"""

import os
import shutil
import sys
import time
import subprocess
import warnings

# 抑制 PyInstaller 钩子中第三方库的 DeprecationWarning（不影响打包）
warnings.filterwarnings('ignore', category=DeprecationWarning, module=r'PyInstaller.*')

# 配置常量
MAIN_SCRIPT = 'serial_GUI.py'
CONFIG_FILE = 'serial_config.json'
APP_NAME = 'SerialTool'
BUILD_TIMEOUT = 600  # 打包超时时间（秒），10分钟

# 尝试多种方式导入PyInstaller
try:
    from PyInstaller import __main__ as pyi_main
    PYINSTALLER_AVAILABLE = True
except ImportError:
    PYINSTALLER_AVAILABLE = False
    print("[警告] 无法直接导入PyInstaller，将尝试使用命令行方式")

def get_path_separator():
    """根据操作系统获取路径分隔符"""
    return ';' if sys.platform == 'win32' else ':'

def clear_old_build():
    """清理旧的构建文件"""
    print("[步骤1] 清理旧的构建文件...")
    try:
        if os.path.exists('build'):
            print("  - 删除 build 目录")
            shutil.rmtree('build', ignore_errors=True)
        if os.path.exists('dist'):
            print("  - 删除 dist 目录")
            shutil.rmtree('dist', ignore_errors=True)
        if os.path.exists(f'{APP_NAME}.spec'):
            print(f"  - 删除 {APP_NAME}.spec 文件")
            os.remove(f'{APP_NAME}.spec')
        print("  [OK] 清理完成")
    except Exception as e:
        print(f"  [警告] 清理过程中出现错误: {e}")
    print("")

def check_dependencies():
    """检查依赖是否安装"""
    print("[步骤2] 检查依赖...")
    try:
        import PyQt5
        import serial
        print("  - PyQt5: OK")
        print("  - pyserial: OK")
    except ImportError as e:
        print(f"  [ERROR] 核心依赖缺失: {e}")
        print("  请运行: pip install pyqt5 pyserial")
        return False
    # 可选依赖（示波器功能需要）
    try:
        import pyqtgraph, numpy
        print("  - pyqtgraph: OK（示波器可用）")
    except ImportError:
        print("  - pyqtgraph: 未安装（示波器将不可用，其他功能正常）")
    print("  [OK] 依赖检查通过")
    return True

def png_to_ico(png_path, max_size=512):
    """将PNG图片转换为ICO格式，支持自适应尺寸"""
    try:
        from PIL import Image
        img = Image.open(png_path)
        
        print(f"  - 原始图片: {img.size}, 模式: {img.mode}")
        
        # 转换为RGB或RGBA模式（确保兼容ICO格式）
        if img.mode == 'P':
            img = img.convert('RGBA')
        elif img.mode == 'LA':
            img = img.convert('RGBA')
        elif img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGBA')
        
        # 创建ICO文件路径
        ico_path = os.path.splitext(png_path)[0] + '.ico'
        
        # 获取原始尺寸
        width, height = img.size
        
        # 限制最大尺寸，避免过大图片导致问题
        if max(width, height) > max_size:
            ratio = max_size / max(width, height)
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            width, height = new_width, new_height
            print(f"  - 缩放至最大尺寸 {max_size}: {width}x{height}")
        
        # 将图片放入正方形透明背景（保持宽高比）
        max_dim = max(width, height)
        square_img = Image.new('RGBA', (max_dim, max_dim), (0, 0, 0, 0))
        
        # 计算居中位置
        paste_x = (max_dim - width) // 2
        paste_y = (max_dim - height) // 2
        square_img.paste(img, (paste_x, paste_y))
        
        print(f"  - 最终尺寸: {square_img.size} (保持宽高比)")
        
        # 根据原图尺寸动态选择合适的图标尺寸
        base_size = max_dim
        sizes = []
        standard_sizes = [256, 128, 64, 48, 32, 16]
        
        for size in standard_sizes:
            if size <= base_size:
                sizes.append((size, size))
        
        # 如果原图很小，确保至少有一个尺寸
        if not sizes:
            sizes = [(min(base_size, 32), min(base_size, 32))]
        
        print(f"  - 生成图标尺寸: {[s[0] for s in sizes]}")
        
        # 调整大小并保存为ICO
        ico_images = []
        ico_sizes = []
        
        for size in sizes:
            resized = square_img.resize(size, Image.Resampling.LANCZOS)
            ico_images.append(resized)
            ico_sizes.append(size)
        
        # 保存为ICO文件
        if len(ico_images) == 1:
            ico_images[0].save(ico_path, format='ICO', sizes=ico_sizes)
        else:
            ico_images[0].save(
                ico_path,
                format='ICO',
                sizes=ico_sizes,
                append_images=ico_images[1:]
            )
        
        # 验证文件是否生成
        if os.path.exists(ico_path):
            file_size = os.path.getsize(ico_path)
            print(f"  - PNG转换为ICO成功: {ico_path} ({file_size} bytes)")
            return ico_path
        else:
            print(f"  - PNG转换为ICO失败: 文件未生成")
            return None
            
    except ImportError:
        print("  - PNG转换为ICO失败: 缺少Pillow库")
        return None
    except Exception as e:
        print(f"  - PNG转换为ICO失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def find_icon():
    """自动检测图标文件，优先ico文件，其次png文件，最后使用默认图标"""
    print("[步骤2.5] 检测图标文件...")
    
    # 在当前目录和icons子目录中搜索
    search_dirs = ['.', 'icons', 'resources']
    
    # 优先搜索ico文件
    for dir_path in search_dirs:
        if os.path.exists(dir_path):
            for filename in os.listdir(dir_path):
                if filename.lower().endswith('.ico'):
                    icon_path = os.path.join(dir_path, filename)
                    print(f"  - 找到图标文件: {icon_path}")
                    return icon_path
    
    # 搜索png文件
    found_png = None
    for dir_path in search_dirs:
        if os.path.exists(dir_path):
            for filename in os.listdir(dir_path):
                if filename.lower().endswith('.png'):
                    found_png = os.path.join(dir_path, filename)
                    break
            if found_png:
                break
    
    # 检查是否可以使用PNG图标
    try:
        import PIL
        pillow_available = True
    except ImportError:
        pillow_available = False
    
    if found_png:
        print(f"  - 找到PNG图标文件: {found_png}")
        if sys.platform == 'win32':
            # Windows平台：尝试将PNG转换为ICO
            print("  - Windows平台需要ICO图标")
            if pillow_available:
                print("  - 尝试将PNG转换为ICO...")
                ico_path = png_to_ico(found_png)
                if ico_path:
                    return ico_path
            else:
                print("  - [警告] 需要安装Pillow库才能将PNG转换为ICO")
                print("    请运行: pip install pillow")
        else:
            # 非Windows平台：可以直接使用PNG
            print("  - PNG图标可用")
            return found_png
    else:
        print("  - 未找到PNG图标文件")
    
    # 如果没有找到图标文件或无法使用，使用默认图标
    print("  - 使用默认图标")
    return None

def verify_main_script():
    """验证主脚本是否存在"""
    print("[步骤2.6] 验证主脚本...")
    if not os.path.exists(MAIN_SCRIPT):
        print(f"  [ERROR] 主脚本不存在: {MAIN_SCRIPT}")
        print(f"  请确保 {MAIN_SCRIPT} 在当前目录中")
        return False
    print(f"  - 主脚本: {MAIN_SCRIPT} (存在)")
    
    # 验证配置文件
    if os.path.exists(CONFIG_FILE):
        print(f"  - 配置文件: {CONFIG_FILE} (存在)")
    else:
        print(f"  [警告] 配置文件不存在: {CONFIG_FILE}")
        print(f"  打包后可能需要手动添加此文件")
    
    print("  [OK] 主脚本验证通过")
    return True

def generate_spec(icon_path=None):
    """生成优化的 spec 文件，排除无用的 DLL、插件、翻译文件"""
    print("[步骤3] 生成优化的 spec 文件...")

    # 使用正斜杠避免 Python 字符串转义问题（\a = bell, \p = ...）
    icon_line = f"    icon=[r'{icon_path.replace(chr(92), '/')}']," if icon_path else ""

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# 只收集 pyqtgraph 核心子模块（排除 examples）
_pg_submodules = collect_submodules('pyqtgraph')
_pg_submodules = [m for m in _pg_submodules if not m.startswith('pyqtgraph.examples')]

hiddenimports = [
    'pyqtgraph', 'numpy',
    'serial.tools.list_ports', 'serial.tools.list_ports_common',
    'serial.tools.list_ports_linux', 'serial.tools.list_ports_windows',
    'serial.tools.list_ports_osx',
] + _pg_submodules

a = Analysis(
    ['{MAIN_SCRIPT}'],
    pathex=[],
    binaries=[],
    datas=[('{CONFIG_FILE}', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=['torch', 'tensorflow', 'tkinter', 'matplotlib', 'PIL', 'cv2'],
    noarchive=False,
    optimize=0,
)

# ── 过滤无用的二进制文件 ──
# 这些是串口工具不需要的 Qt 组件（OpenGL、QML、蓝牙等），
# 以及多余的插件和翻译文件
EXCLUDE_BINARIES = {{
    # OpenGL 相关 —— 串口工具不需要 3D 渲染
    'opengl32sw.dll', 'd3dcompiler_47.dll', 'libGLESv2.dll', 'libEGL.dll',
    # QML 引擎 —— 纯 Widgets 应用不需要
    'Qt5Quick.dll', 'Qt5Qml.dll', 'Qt5QmlModels.dll',
    'Qt5QuickTemplates2.dll', 'Qt5QuickParticles.dll',
    'Qt5Quick3D.dll', 'Qt5Quick3DRuntimeRender.dll',
    'Qt5QuickTest.dll', 'Qt5QuickControls2.dll', 'Qt5QuickShapes.dll',
    # 无关网络/硬件模块
    'Qt5WebSockets.dll', 'Qt5Bluetooth.dll', 'Qt5Nfc.dll',
    'Qt5Location.dll', 'Qt5Positioning.dll', 'Qt5PositioningQuick.dll',
    'Qt5Sensors.dll', 'Qt5SerialPort.dll', 'Qt5SerialBus.dll',
    # Linux 桌面组件
    'Qt5DBus.dll', 'Qt5X11Extras.dll',
    # 多余模块
    'Qt5Designer.dll', 'Qt5DesignerComponents.dll', 'Qt5Help.dll',
    'Qt5XmlPatterns.dll', 'Qt5Test.dll', 'Qt5Svg.dll',
    'Qt5Multimedia.dll', 'Qt5MultimediaWidgets.dll',
    'Qt5RemoteObjects.dll',
    # Crypto/SSL —— Qt 自带的，优先用系统的
    'libcrypto-1_1-x64.dll', 'libssl-1_1-x64.dll', 'libeay32.dll',
    # Windows 系统 DLL —— 不需要打包
    'msvcp140.dll', 'msvcp140_1.dll', 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll',
    'concrt140.dll', 'vccorlib140.dll',
}}

# 需要保留的 Qt 插件白名单
KEEP_PLATFORMS = {{'qwindows.dll'}}        # 只需要 Windows 平台插件
KEEP_STYLES = {{'qwindowsvistastyle.dll'}}  # 只需要 Windows 风格
KEEP_ICON_ENGINES = {{'qsvgicon.dll'}}      # SVG 图标引擎
# 保留常用图片格式
KEEP_IMAGE_FORMATS = {{'qjpeg.dll', 'qgif.dll', 'qico.dll', 'qsvg.dll'}}
KEEP_GENERIC = set()                        # 不需要 generic 插件
KEEP_PLATFORM_THEMES = set()                # 不需要 platformthemes

# 只保留中文和英文翻译
KEEP_TRANSLATIONS = {{'qt_zh_CN.qm', 'qt_zh_TW.qm', 'qt_en.qm',
                       'qtbase_zh_CN.qm', 'qtbase_zh_TW.qm', 'qtbase_en.qm'}}

filtered_binaries = []
removed_count = 0
removed_size = 0
for (name, path, typ) in a.binaries:
    basename = os.path.basename(name)
    # 检查排除列表
    if basename in EXCLUDE_BINARIES:
        removed_count += 1
        removed_size += os.path.getsize(path) if os.path.exists(path) else 0
        continue
    # 检查 Qt 插件 —— 只保留白名单
    if '/plugins/platforms/' in name.replace('\\\\', '/'):
        if basename not in KEEP_PLATFORMS:
            removed_count += 1; continue
    if '/plugins/styles/' in name.replace('\\\\', '/'):
        if basename not in KEEP_STYLES:
            removed_count += 1; continue
    if '/plugins/iconengines/' in name.replace('\\\\', '/'):
        if basename not in KEEP_ICON_ENGINES:
            removed_count += 1; continue
    if '/plugins/imageformats/' in name.replace('\\\\', '/'):
        if basename not in KEEP_IMAGE_FORMATS:
            removed_count += 1; continue
    if '/plugins/generic/' in name.replace('\\\\', '/'):
        if basename not in KEEP_GENERIC:
            removed_count += 1; continue
    if '/plugins/platformthemes/' in name.replace('\\\\', '/'):
        if basename not in KEEP_PLATFORM_THEMES:
            removed_count += 1; continue
    # 检查翻译文件
    if '/translations/' in name.replace('\\\\', '/'):
        if basename not in KEEP_TRANSLATIONS:
            removed_count += 1; continue
    # 排除 numpy 测试模块
    if '_multiarray_tests' in basename:
        removed_count += 1; continue
    # 排除 pyqtgraph 大图标
    if 'pyqtgraph/icons/' in name.replace('\\\\', '/'):
        removed_count += 1; continue
    filtered_binaries.append((name, path, typ))

print(f"  [优化] 移除了 {{removed_count}} 个无用文件 (约 {{removed_size/1024/1024:.1f}} MB)")
print(f"  [优化] 保留 {{len(filtered_binaries)}} 个文件")
a.binaries = filtered_binaries

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='{APP_NAME}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
{icon_line}
)
'''

    spec_path = f'{APP_NAME}.spec'
    with open(spec_path, 'w', encoding='utf-8') as f:
        f.write(spec_content)
    print(f"  [OK] spec 文件已生成: {spec_path}")
    return spec_path


def build_application(icon_path=None):
    """构建应用程序（使用优化的 spec 文件）"""
    print("[步骤3] 开始打包...")

    # 生成优化的 spec 文件
    spec_path = generate_spec(icon_path)

    print("  正在构建...")
    start_time = time.time()

    # 使用 spec 文件构建
    if PYINSTALLER_AVAILABLE:
        try:
            pyi_main.run([spec_path])
            end_time = time.time()
            print(f"  构建完成，用时: {end_time - start_time:.2f}秒")
            return True
        except KeyboardInterrupt:
            print("\n  [中断] 用户取消打包")
            raise
        except Exception as e:
            print(f"  [警告] API方式构建失败: {e}")
            print("  尝试使用命令行方式...")

    # 命令行方式
    try:
        cmd = [sys.executable, '-m', 'PyInstaller', spec_path]
        print(f"  执行命令: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=BUILD_TIMEOUT
        )
        if result.returncode == 0:
            end_time = time.time()
            print(f"  构建完成，用时: {end_time - start_time:.2f}秒")
            return True
        else:
            print(f"  [ERROR] 构建失败:")
            print(f"  错误输出: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] 构建超时（超过{BUILD_TIMEOUT}秒）")
        return False
    except KeyboardInterrupt:
        print("\n  [中断] 用户取消打包")
        raise
    except Exception as e:
        print(f"  [ERROR] 命令行方式构建失败: {e}")
        return False

def verify_build():
    """验证构建结果"""
    print("[步骤4] 验证构建结果...")
    
    if sys.platform == 'win32':
        exe_name = f'{APP_NAME}.exe'
    else:
        exe_name = APP_NAME
    exe_path = os.path.join('dist', exe_name)
    if os.path.exists(exe_path):
        file_size = os.path.getsize(exe_path) / (1024 * 1024)  # MB
        print(f"  - 可执行文件: {exe_path}")
        print(f"  - 文件大小: {file_size:.2f} MB")
        print("  [OK] 构建成功！")
        return True
    else:
        print("  [ERROR] 构建失败，可执行文件未生成")
        return False

def main():
    """主函数"""
    print("========================================")
    print("  串口调试助手 - 打包脚本")
    print("========================================")
    print("")
    
    try:
        # 步骤1: 清理旧文件
        clear_old_build()
        
        # 步骤2: 检查依赖
        if not check_dependencies():
            print("\n[ERROR] 打包终止")
            input("按Enter键退出...")
            return
        print("")
        
        # 步骤2.5: 查找图标文件
        icon_path = find_icon()
        print("")
        
        # 步骤2.6: 验证主脚本
        if not verify_main_script():
            print("\n[ERROR] 打包终止")
            input("按Enter键退出...")
            return
        print("")
        
        # 步骤3: 构建应用
        if not build_application(icon_path):
            print("\n[ERROR] 打包终止")
            input("按Enter键退出...")
            return
        print("")
        
        # 步骤4: 验证构建
        if verify_build():
            print("\n========================================")
            print("  [SUCCESS] 打包成功！")
            print(f"  输出文件: dist\\{APP_NAME}.exe")
            print("  可以直接运行此文件，无需Python环境")
            print("========================================")
        else:
            print("\n❌ 打包失败")
        
        print("")
        # 非交互模式下跳过 input
        try:
            input("按Enter键退出...")
        except EOFError:
            pass
    except KeyboardInterrupt:
        print("\n\n[中断] 打包过程已被用户取消")
        sys.exit(1)

if __name__ == '__main__':
    main()
