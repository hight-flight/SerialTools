# 跨平台发布加固实现计划

> **给 agent 执行者：** 必需子 skill：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，逐任务实现本计划。步骤使用复选框（`- [ ]`）语法进行跟踪。

**目标：** 修复 Windows 与 Ubuntu 22.04 发布审查中发现的路径、兼容性、构建安全、传输可靠性和发布可复现性问题。

**架构：** 运行时目录统一由 `app_paths.py` 决定，构建脚本只操作由脚本位置派生并验证过的目录。Ubuntu 22.04 作为兼容基线，发布包通过锁定依赖、固定归档元数据和自动化冒烟测试保证稳定性。

**技术栈：** Python 3.10+、PyQt5、PyInstaller、unittest、Bash、Inno Setup、Debian 打包工具。

---

### 任务 1：Windows 用户目录和旧数据迁移

**文件：**
- 修改：`app_paths.py`
- 修改：`serial_GUI.py`
- 测试：`tests/test_app_paths.py`

- [ ] **步骤 1：编写失败测试**

新增测试，断言 Windows 配置使用 `APPDATA`，数据和缓存使用 `LOCALAPPDATA`；旧工作目录配置首次迁移到新配置目录。

- [ ] **步骤 2：运行测试，确认失败**

运行：`python -m unittest tests.test_app_paths -v`

预期：Windows 路径仍等于当前工作目录，迁移函数不存在。

- [ ] **步骤 3：编写最小实现**

为 Windows 解析用户目录，增加只迁移明确配置文件的幂等函数，并在主窗口初始化时调用。

- [ ] **步骤 4：运行测试，确认通过**

运行：`python -m unittest tests.test_app_paths -v`

预期：全部通过。

### 任务 2：Ubuntu 22.04 构建基线和输出目录保护

**文件：**
- 修改：`build_ubuntu.sh`
- 修改：`packaging/linux/build_linux.py`
- 测试：`tests/test_linux_packaging.py`

- [ ] **步骤 1：编写失败测试**

新增测试，断言正式包装脚本只接受 Ubuntu 22.04；输出目录位于 `build/linux` 内时明确拒绝；归档元数据使用固定时间。

- [ ] **步骤 2：运行测试，确认失败**

运行：`python -m unittest tests.test_linux_packaging -v`

预期：版本检查、输出目录保护或固定时间断言失败。

- [ ] **步骤 3：编写最小实现**

限制 Ubuntu 版本为 22.04，校验输出目录，使用 `SOURCE_DATE_EPOCH` 规范化 tar 成员和 gzip 时间。

- [ ] **步骤 4：运行测试，确认通过**

在 Windows 和 Ubuntu 22.04 分别运行 Linux 打包测试，预期全部通过。

### 任务 3：Windows 构建清理与供应链安全

**文件：**
- 修改：`build_app.py`
- 创建：`tests/test_windows_packaging.py`

- [ ] **步骤 1：编写失败测试**

新增测试，断言项目路径从 `__file__` 推导，清理函数拒绝项目外目录，构建不自动下载未经校验的 UPX，也不打包本地 `serial_config.json`。

- [ ] **步骤 2：运行测试，确认失败**

运行：`python -m unittest tests.test_windows_packaging -v`

预期：安全清理 API 不存在或自动下载逻辑仍存在。

- [ ] **步骤 3：编写最小实现**

让构建全过程使用项目绝对路径；清理前执行边界检查；仅使用系统或本地已有 UPX；删除配置文件打包行为。

- [ ] **步骤 4：运行测试，确认通过**

运行 Windows 打包测试，预期全部通过。

### 任务 4：TCP 连接状态和完整发送

**文件：**
- 修改：`transport.py`
- 创建：`tests/test_transport.py`

- [ ] **步骤 1：编写失败测试**

使用真实 `socket.socketpair()` 验证远端断开后 `is_open` 为假，TCP 客户端和服务端发送使用完整发送语义。

- [ ] **步骤 2：运行测试，确认失败**

运行：`python -m unittest tests.test_transport -v`

预期：远端断开后 `is_open` 仍为真。

- [ ] **步骤 3：编写最小实现**

识别 EOF 并关闭对应连接；TCP 写入使用 `sendall()`，成功时返回数据长度。

- [ ] **步骤 4：运行测试，确认通过**

运行传输层测试，预期全部通过。

### 任务 5：依赖锁定、CI、打包优化和文档

**文件：**
- 创建：`requirements-release.txt`
- 创建：`.github/workflows/cross-platform.yml`
- 修改：`requirements.txt`
- 修改：`build_app.py`
- 修改：`README.md`
- 修改：`packaging/linux/README.md`
- 修改：`Ubuntu打包使用说明.md`
- 删除：`SerialTool.iss`

- [ ] **步骤 1：编写失败检查**

在打包测试中断言发布依赖使用精确版本、Windows 不收集全部 pyqtgraph 子模块、生成的 Inno Setup 内容使用当前版本且不依赖已提交的绝对路径文件。

- [ ] **步骤 2：运行测试，确认失败**

运行全部测试，预期新增发布约束断言失败。

- [ ] **步骤 3：编写最小实现**

增加发布锁定文件；精简 PyInstaller 收集项；添加 Windows/Ubuntu CI；同步相对路径文档；删除过期生成文件。

- [ ] **步骤 4：运行完整验证**

运行 Windows 和 Ubuntu 单元测试、Python 编译检查、Bash 语法检查、Windows onedir 构建及 Linux 产物检查。

- [ ] **步骤 5：提交**

按任务形成独立中文提交，最终工作区保持干净。
