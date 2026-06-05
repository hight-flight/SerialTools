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
        print("  [OK] 依赖检查通过")
        return True
    except ImportError as e:
        print(f"  [ERROR] 依赖缺失: {e}")
        print("  请运行: pip install -r requirements.txt")
        return False

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

def build_application(icon_path=None):
    """构建应用程序"""
    print("[步骤3] 开始打包...")
    
    # 获取跨平台路径分隔符
    path_sep = get_path_separator()
    
    # 打包参数
    args = [
        '--onefile',          # 生成单个可执行文件
        '--windowed',         # 无命令行窗口（GUI应用）
        '--name', APP_NAME,   # 可执行文件名
        '--add-data', f'{CONFIG_FILE}{path_sep}.',  # 添加配置文件（跨平台兼容）
        '--hidden-import', 'pyqtgraph',  # 示波器懒加载依赖
        '--hidden-import', 'numpy',      # pyqtgraph 底层依赖
        MAIN_SCRIPT           # 主脚本
    ]
    
    # 添加图标参数 (插入到脚本名称之前)
    if icon_path:
        args.insert(-1, '--icon')
        args.insert(-1, icon_path)
    
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
        #input("按Enter键退出...")
    except KeyboardInterrupt:
        print("\n\n[中断] 打包过程已被用户取消")
        sys.exit(1)

if __name__ == '__main__':
    main()
