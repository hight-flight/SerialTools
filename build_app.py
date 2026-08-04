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
import argparse
import warnings
from pathlib import Path

# 抑制 PyInstaller 钩子中第三方库的 DeprecationWarning（不影响打包）
warnings.filterwarnings('ignore', category=DeprecationWarning, module=r'PyInstaller.*')

# 配置常量
PROJECT_ROOT = Path(__file__).resolve().parent
MAIN_SCRIPT = 'serial_GUI.py'
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


def get_app_version():
    """从 theme.py 读取版本号（加密源码运行时可正常 import）；失败回退 1.0.0"""
    try:
        from theme import VERSION  # type: ignore
        return str(VERSION)
    except Exception:
        return '1.0.0'


def find_iscc():
    """定位 Inno Setup 编译器 ISCC.exe；未找到返回 None"""
    if sys.platform != 'win32':
        return None
    # 1) PATH 中查找
    for candidate in ('ISCC.exe', 'iscc.exe'):
        from shutil import which
        found = which(candidate)
        if found:
            return found
    # 2) 常见安装路径
    common_dirs = [
        r'C:\Program Files (x86)\Inno Setup 6',
        r'C:\Program Files\Inno Setup 6',
        r'C:\Program Files (x86)\Inno Setup 5',
        r'C:\Program Files\Inno Setup 5',
    ]
    for d in common_dirs:
        p = os.path.join(d, 'ISCC.exe')
        if os.path.exists(p):
            return p
    return None


def generate_iss(version, icon_path=None):
    """生成 Inno Setup 脚本 SerialTool.iss，返回脚本路径"""
    iss_path = os.path.abspath(f'{APP_NAME}.iss')
    icon_line = f'SetupIconFile={os.path.abspath(icon_path)}' if icon_path else ''

    # Inno Setup 模板：把 onedir 产物 dist/SerialTool/ 打成单文件安装包
    iss_content = f"""\
; 由 build_app.py 自动生成，请勿手动编辑
#define MyAppName "{APP_NAME}"
#define MyAppVersion "{version}"
#define MyAppExeName "{APP_NAME}.exe"
#define MyAppSourceDir "dist\\{APP_NAME}"

[Setup]
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={APP_NAME}
DefaultDirName={{autopf}}\\{{#MyAppName}}
DefaultGroupName={{#MyAppName}}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename={APP_NAME}_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}
{icon_line}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"; Flags: unchecked

[Files]
Source: "{{#MyAppSourceDir}}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; WorkingDir: "{{app}}"
Name: "{{group}}\\{{cm:UninstallProgram,{{#MyAppName}}}}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; WorkingDir: "{{app}}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "{{cm:LaunchProgram,{{#MyAppName}}}}"; Flags: nowait postinstall skipifsilent
"""
    with open(iss_path, 'w', encoding='utf-8') as f:
        f.write(iss_content)
    return iss_path


def build_setup(icon_path=None):
    """用 Inno Setup 把 onedir 产物打包成单文件安装包 SerialTool_Setup.exe"""
    print("[步骤5] 生成安装包（Inno Setup）...")

    iscc = find_iscc()
    if not iscc:
        print("  [警告] 未找到 Inno Setup 编译器（ISCC.exe），跳过安装包生成。")
        print("         请安装 Inno Setup 6：https://jrsoftware.org/isdl.php")
        print("         安装后重新运行：python build_app.py --setup")
        return False

    version = get_app_version()
    print(f"  - 版本号: {version}")
    print(f"  - ISCC: {iscc}")

    iss_path = generate_iss(version, icon_path)
    print(f"  - 脚本: {iss_path}")

    cmd = [iscc, '/Q', iss_path]  # /Q 静默，仅输出错误
    print("  - 正在编译安装包...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=BUILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] 编译超时（超过{BUILD_TIMEOUT}秒）")
        return False

    if result.returncode != 0:
        print("  [ERROR] Inno Setup 编译失败：")
        print(result.stdout)
        print(result.stderr)
        return False

    setup_exe = os.path.join('dist', f'{APP_NAME}_Setup.exe')
    if os.path.exists(setup_exe):
        file_size = os.path.getsize(setup_exe) / (1024 * 1024)  # MB
        print(f"  - 安装包: {setup_exe}")
        print(f"  - 文件大小: {file_size:.2f} MB")
        print("  [OK] 安装包生成成功！")
        return True
    else:
        print("  [ERROR] 安装包未生成")
        print(result.stdout)
        return False


def clear_old_build(project_root=PROJECT_ROOT):
    """只清理已验证项目根目录内的构建产物。"""
    root = Path(project_root).resolve()
    if not (root / MAIN_SCRIPT).is_file():
        raise RuntimeError(f"拒绝清理：目录不是有效的 SerialTool 项目：{root}")

    print("[步骤1] 清理旧的构建文件...")
    try:
        build_dir = root / 'build'
        dist_dir = root / 'dist'
        spec_file = root / f'{APP_NAME}.spec'
        if build_dir.exists():
            print("  - 删除 build 目录")
            shutil.rmtree(build_dir)
        if dist_dir.exists():
            print("  - 删除 dist 目录")
            shutil.rmtree(dist_dir)
        if spec_file.exists():
            print(f"  - 删除 {APP_NAME}.spec 文件")
            spec_file.unlink()
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
    # PNG转ICO需要Pillow
    try:
        from PIL import Image
        print("  - Pillow: OK（PNG转ICO可用）")
    except ImportError:
        print("  - Pillow: 未安装（PNG图标无法转换为ICO，请运行: pip install pillow）")
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
    
    # 优先搜索ico文件：优先匹配名称含"图标"/"icon"/"app"的文件
    icon_keywords = ['图标', 'icon', 'app', 'logo']
    for dir_path in search_dirs:
        if os.path.exists(dir_path):
            all_icos = [f for f in os.listdir(dir_path) if f.lower().endswith('.ico')]
            for kw in icon_keywords:
                for f in all_icos:
                    if kw in f.lower():
                        icon_path = os.path.join(dir_path, f)
                        print(f"  - 找到图标文件: {icon_path}")
                        return icon_path
            # 没有关键词匹配时，取第一个
            if all_icos:
                icon_path = os.path.join(dir_path, all_icos[0])
                print(f"  - 找到图标文件: {icon_path}")
                return icon_path
    
    # 搜索png文件：优先匹配名称含"图标"/"icon"/"app"的文件，避免误选UI素材
    found_png = None
    icon_keywords = ['图标', 'icon', 'app', 'logo']
    for dir_path in search_dirs:
        if os.path.exists(dir_path):
            all_pngs = [f for f in os.listdir(dir_path) if f.lower().endswith('.png')]
            # 优先匹配含关键词的文件名
            for kw in icon_keywords:
                for f in all_pngs:
                    if kw in f.lower():
                        found_png = os.path.join(dir_path, f)
                        break
                if found_png:
                    break
            # 没有关键词匹配时，取第一个
            if not found_png and all_pngs:
                found_png = os.path.join(dir_path, all_pngs[0])
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
    print("  [OK] 主脚本验证通过")
    return True

def find_upx(project_root=PROJECT_ROOT):
    """查找 UPX 压缩工具，返回 (已安装, 目录路径)

    Returns:
        (True, None)    - UPX 在系统 PATH 上，无需 --upx-dir
        (True, upx_dir)  - UPX 在本地 upx/ 目录，需 --upx-dir <upx_dir>
        (False, None)   - 未找到
    """
    # 1) PATH 中查找（PyInstaller 会自动搜索 PATH，无需 --upx-dir）
    if shutil.which('upx') or shutil.which('upx.exe'):
        print("  - UPX: 已安装（系统 PATH 中）")
        return (True, None)

    # 2) 检查 upx 子目录
    upx_dir = os.path.join(os.fspath(Path(project_root).resolve()), 'upx')
    upx_exe = os.path.join(upx_dir, 'upx.exe')
    if os.path.exists(upx_exe):
        print(f"  - UPX: 已安装（本地目录: {upx_dir}）")
        return (True, upx_dir)

    print("  - UPX: 未安装，将继续构建且不启用压缩")
    print("    如需压缩，请从 UPX 官方发布页安装并自行验证文件校验值。")
    return (False, None)


def build_pyinstaller_arguments(
    icon_path=None,
    onedir=False,
    upx_dir=None,
):
    """生成 Windows PyInstaller 参数，不包含任何本地用户配置。"""
    args = [
        '--onedir' if onedir else '--onefile',
        '--windowed',
        '--name', APP_NAME,
        '--hidden-import', 'pyqtgraph',
        '--hidden-import', 'numpy',
        '--collect-submodules', 'pyqtgraph',
        '--hidden-import', 'ota_center',
        '--hidden-import', 'gsm_debugger',
        '--hidden-import', 'auto_reply',
        '--hidden-import', 'data_viewer',
        '--hidden-import', 'http.server',
        '--hidden-import', 'socketserver',
        '--hidden-import', 'serial.tools.list_ports',
        '--hidden-import', 'serial.tools.list_ports_common',
        '--hidden-import', 'serial.tools.list_ports_linux',
        '--hidden-import', 'serial.tools.list_ports_windows',
        '--hidden-import', 'serial.tools.list_ports_osx',
        '--exclude-module', 'PyQt5.QtWebEngine',
        '--exclude-module', 'PyQt5.QtWebEngineWidgets',
        '--exclude-module', 'PyQt5.QtWebChannel',
        '--exclude-module', 'PyQt5.QtBluetooth',
        '--exclude-module', 'PyQt5.QtMultimedia',
        '--exclude-module', 'PyQt5.QtMultimediaWidgets',
        '--exclude-module', 'PyQt5.QtSql',
        '--exclude-module', 'PyQt5.QtTest',
        '--exclude-module', 'PyQt5.QtHelp',
        '--exclude-module', 'PyQt5.QtDesigner',
        '--exclude-module', 'PyQt5.QtSensors',
        '--exclude-module', 'PyQt5.QtPositioning',
        '--exclude-module', 'PyQt5.QtQml',
        '--exclude-module', 'PyQt5.QtQuick',
        '--exclude-module', 'PyQt5.QtQuickWidgets',
        '--exclude-module', 'PyQt5.QtDBus',
        '--exclude-module', 'PyQt5.QtXmlPatterns',
        '--exclude-module', 'torch',
        '--exclude-module', 'tensorflow',
        '--exclude-module', 'tkinter',
        '--exclude-module', 'matplotlib',
        '--exclude-module', 'PIL',
        '--exclude-module', 'cv2',
        '--exclude-module', 'scipy',
        '--exclude-module', 'pandas',
        '--exclude-module', 'notebook',
        '--exclude-module', 'jupyter',
        '--exclude-module', 'cryptography',
        '--exclude-module', 'lxml',
        '--exclude-module', 'h5py',
        '--exclude-module', 'bs4',
        '--exclude-module', 'zmq',
        '--exclude-module', 'IPython',
        '--exclude-module', 'bokeh',
        '--exclude-module', 'sympy',
        '--exclude-module', 'dateutil',
        '--exclude-module', 'unittest',
        '--exclude-module', 'doctest',
        '--exclude-module', 'pdb',
        '--exclude-module', 'lib2to3',
        '--exclude-module', 'turtledemo',
        '--exclude-module', 'distutils',
        '--exclude-module', 'setuptools',
        '--exclude-module', 'pkg_resources',
        '--exclude-module', 'curses',
    ]
    if onedir:
        args.append('--noupx')
    if upx_dir:
        args.extend(['--upx-dir', os.fspath(upx_dir)])
    if icon_path:
        args.extend(['--icon', os.fspath(icon_path)])
    args.append(MAIN_SCRIPT)
    return args


def build_application(icon_path=None, onedir=False):
    """构建应用程序

    onedir=False（默认）：--onefile 单文件模式，产物为 dist/SerialTool.exe
    onedir=True：--onedir 目录模式 + --noupx，启动更快（首启无需解压临时目录、
                 避免 UPX 解压与杀软扫描开销），产物为 dist/SerialTool/ 文件夹
    """
    print("[步骤3] 开始打包...")

    if onedir:
        print("  [模式] --onedir 目录模式（已启用 --noupx，启动更快）")
    else:
        print("  [模式] --onefile 单文件模式（默认）")

    # 单文件模式下查找 UPX 压缩工具（可减少 30-50% 体积）
    upx_ok = False
    upx_dir = None
    if not onedir:
        upx_ok, upx_dir = find_upx()
        if upx_ok:
            print("  [优化] UPX 压缩已启用（可减少 EXE 体积 30-50%）")
    print("")

    args = build_pyinstaller_arguments(
        icon_path=icon_path,
        onedir=onedir,
        upx_dir=upx_dir if upx_ok else None,
    )
    
    print(f"  打包参数: {' '.join(args)}")
    print("  正在构建...")
    
    start_time = time.time()
    
    # 尝试使用API方式
    if PYINSTALLER_AVAILABLE:
        try:
            pyi_main.run(args)
            end_time = time.time()
            print(f"  构建完成，用时: {end_time - start_time:.2f}秒")
            return True
        except KeyboardInterrupt:
            print("\n  [中断] 用户取消打包")
            raise
        except Exception as e:
            print(f"  [警告] API方式构建失败: {e}")
            print("  尝试使用命令行方式...")
    
    # 尝试使用命令行方式
    try:
        # 构建命令
        cmd = [sys.executable, '-m', 'PyInstaller'] + args
        print(f"  执行命令: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True,
            timeout=BUILD_TIMEOUT  # 设置超时时间
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
        print("  尝试使用pyinstaller直接调用...")
    
    # 尝试直接调用pyinstaller可执行文件
    try:
        # 尝试找到pyinstaller可执行文件
        possible_paths = [
            os.path.join(os.path.dirname(sys.executable), 'Scripts', 'pyinstaller.exe'),
            os.path.join(os.path.dirname(sys.executable), 'pyinstaller.exe'),
            'pyinstaller.exe',
            'pyinstaller'
        ]
        
        pyinstaller_path = None
        for path in possible_paths:
            if os.path.exists(path):
                pyinstaller_path = path
                break
        
        if pyinstaller_path:
            cmd = [pyinstaller_path] + args
            print(f"  执行命令: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True,
                timeout=BUILD_TIMEOUT  # 设置超时时间
            )
            if result.returncode == 0:
                end_time = time.time()
                print(f"  构建完成，用时: {end_time - start_time:.2f}秒")
                return True
            else:
                print(f"  [ERROR] 构建失败:")
                print(f"  错误输出: {result.stderr}")
                return False
        else:
            print("  [ERROR] 找不到pyinstaller可执行文件")
            print("  请确保PyInstaller已正确安装:")
            print("  pip install pyinstaller")
            return False
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] 构建超时（超过{BUILD_TIMEOUT}秒）")
        return False
    except KeyboardInterrupt:
        print("\n  [中断] 用户取消打包")
        raise
    except Exception as e:
        print(f"  [ERROR] 直接调用构建失败: {e}")
        return False

def verify_build(onedir=False):
    """验证构建结果"""
    print("[步骤4] 验证构建结果...")

    if sys.platform == 'win32':
        exe_name = f'{APP_NAME}.exe'
    else:
        exe_name = APP_NAME

    if onedir:
        # 目录模式产物：dist/SerialTool/SerialTool.exe
        exe_path = os.path.join('dist', APP_NAME, exe_name)
    else:
        exe_path = os.path.join('dist', exe_name)

    if os.path.exists(exe_path):
        file_size = os.path.getsize(exe_path) / (1024 * 1024)  # MB
        print(f"  - 可执行文件: {exe_path}")
        print(f"  - 文件大小: {file_size:.2f} MB")
        if onedir:
            print(f"  - 产物目录: dist\\{APP_NAME}\\（分发时需整目录拷贝）")
        print("  [OK] 构建成功！")
        return True
    else:
        print("  [ERROR] 构建失败，可执行文件未生成")
        return False

def main():
    """主函数"""
    # 命令行参数：--onedir 切换为目录模式（默认仍为 onefile 单文件模式）
    parser = argparse.ArgumentParser(description='串口调试助手 - 打包脚本')
    parser.add_argument('--onedir', action='store_true',
                        help='使用 --onedir 目录模式打包（启动更快，产物为文件夹）；'
                             '默认为 --onefile 单文件模式')
    parser.add_argument('--setup', action='store_true',
                        help='生成 Inno Setup 安装包（自动启用 --onedir，'
                             '产物为 dist/SerialTool_Setup.exe，需先安装 Inno Setup 6）')
    args_cli = parser.parse_args()
    os.chdir(PROJECT_ROOT)

    # --setup 隐含 onedir（安装包基于 onedir 产物）
    onedir_mode = args_cli.onedir or args_cli.setup

    print("========================================")
    print("  串口调试助手 - 打包脚本")
    print("========================================")
    print("")

    try:
        # 步骤1: 清理旧文件
        clear_old_build(PROJECT_ROOT)
        
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
        if not build_application(icon_path, onedir=onedir_mode):
            print("\n[ERROR] 打包终止")
            input("按Enter键退出...")
            return
        print("")

        # 步骤4: 验证构建
        if not verify_build(onedir=onedir_mode):
            print("\n❌ 打包失败")
            return
        print("")

        # 步骤5: 生成安装包（仅 --setup）
        if args_cli.setup:
            if not build_setup(icon_path):
                print("\n[警告] 安装包生成未完成（onedir 产物已可用）")
            print("")

        print("========================================")
        print("  [SUCCESS] 打包成功！")
        if args_cli.setup and os.path.exists(os.path.join('dist', f'{APP_NAME}_Setup.exe')):
            print(f"  安装包: dist\\{APP_NAME}_Setup.exe（单文件，可直接分发安装）")
        elif onedir_mode:
            print(f"  输出目录: dist\\{APP_NAME}\\")
            print(f"  入口文件: dist\\{APP_NAME}\\{APP_NAME}.exe")
            print("  分发时需拷贝整个目录")
        else:
            print(f"  输出文件: dist\\{APP_NAME}.exe")
        print("  可以直接运行此文件，无需Python环境")
        print("========================================")

        print("")
        #input("按Enter键退出...")
    except KeyboardInterrupt:
        print("\n\n[中断] 打包过程已被用户取消")
        sys.exit(1)

if __name__ == '__main__':
    main()
