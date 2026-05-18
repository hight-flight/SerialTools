# 版本信息配置说明

## 概述
本项目使用 `version_info.txt` 文件来定义 Windows exe 可执行文件的版本信息。该文件会在打包时通过 PyInstaller 的 `--version-file` 参数嵌入到生成的 exe 文件中。

## 文件位置
- 版本信息文件：`version_info.txt`（项目根目录）
- 打包脚本：`build_app.py`

## 如何修改版本号

### 1. 打开 version_info.txt 文件

### 2. 修改以下字段：

#### 文件版本和产品版本（数字格式）
```python
filevers=(1, 0, 0, 0),  # 文件格式：(主版本, 次版本, 修订号, 构建号)
prodvers=(1, 0, 0, 0),  # 产品版本格式同上
```

#### 字符串版本信息
```python
StringStruct(u'FileVersion', u'1.0.0.0'),        # 文件版本字符串
StringStruct(u'ProductVersion', u'1.0.0.0'),     # 产品版本字符串
```

#### 其他信息
```python
StringStruct(u'CompanyName', u'您的公司名称'),
StringStruct(u'FileDescription', u'文件描述'),
StringStruct(u'LegalCopyright', u'版权信息'),
StringStruct(u'ProductName', u'产品名称'),
```

## 示例：将版本从 1.0.0.0 升级到 1.1.0.0

修改前：
```python
filevers=(1, 0, 0, 0),
prodvers=(1, 0, 0, 0),
StringStruct(u'FileVersion', u'1.0.0.0'),
StringStruct(u'ProductVersion', u'1.0.0.0')
```

修改后：
```python
filevers=(1, 1, 0, 0),
prodvers=(1, 1, 0, 0),
StringStruct(u'FileVersion', u'1.1.0.0'),
StringStruct(u'ProductVersion', u'1.1.0.0')
```

## 打包流程

1. 修改 `version_info.txt` 中的版本信息
2. 运行打包命令：
   ```bash
   python build_app.py
   ```
3. 打包完成后，在 `dist` 目录下找到生成的 exe 文件
4. 右键点击 exe 文件 → 属性 → 详细信息，查看版本信息

## 注意事项

- 版本号必须遵循四段式格式：`(主版本, 次版本, 修订号, 构建号)`
- 字符串版本号和数字版本号应保持同步
- 如果 `version_info.txt` 文件不存在，打包脚本会自动跳过版本信息添加
- 版本信息仅在 Windows 平台上有效

## 验证版本信息

打包完成后，您可以通过以下方式验证版本信息：

1. **Windows 资源管理器**：
   - 右键点击 exe 文件
   - 选择"属性"
   - 切换到"详细信息"标签页
   - 查看各项版本信息

2. **命令行方式**：
   ```powershell
   (Get-Item .\SerialTool.exe).VersionInfo
   ```
