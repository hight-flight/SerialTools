"""SerialTool 自动化测试。"""

import os
from pathlib import Path
import subprocess
import sys


def run_on_native_windows(test, platform_name, module_name):
    """离屏后端无法验证 Windows 原生弹窗和窗口外框，单独启动原生用例。"""
    if os.name != "nt" or platform_name == "windows":
        return False
    test_name = f"{module_name}.{type(test).__name__}.{test._testMethodName}"
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-m", "unittest", test_name],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "windows"},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    test.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    return True
