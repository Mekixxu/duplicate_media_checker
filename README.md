# 电影文件管理与去重工具 (Movie Manager)

这是一个基于 Python 的电影文件管理工具，专为 Windows LTSC 2021 等高性能环境设计。它能够扫描本地磁盘、SMB 网络共享和 OneDrive，提取视频元数据，识别不同版本（如导演剪辑版、多语言文件名）的同一部电影，并生成交互式的 HTML 报告。

## 功能特点

1.  **全能扫描**：支持本地路径 (`D:\Movies`) 和 SMB 网络路径 (`\\NAS\Movies`).
2.  **智能匹配**：
    *   **时长指纹**：通过 FFprobe 精确提取视频时长，作为判断同一部电影的核心依据（误差容限 1%）。
    *   **模糊匹配**：使用 RapidFuzz 算法处理文件名差异。
    *   **番号识别**：自动识别类似 `RBC-1007` 的特殊编号。
    *   **跨语言识别**：(可选) 通过 TMDB API 自动获取电影的中英文别名，实现“英国病人”与“The English Patient”的自动关联。
3.  **交互式报告**：生成的 HTML 报告包含完整数据，支持复杂的自定义筛选规则。
4.  **Web 服务模式**：内置轻量级 Web 服务器，支持在浏览器中直接点击打开文件或定位文件。

## 安装依赖

确保你已经安装了 Python 3.8+。

1.  安装 Python 库：
    ```bash
    pip install -r movie_manager/requirements.txt
    ```

2.  **安装 FFmpeg** (关键):
    程序依赖 `ffprobe` 来获取视频时长。请确保 `ffprobe` 在你的系统 PATH 环境变量中。
    *   下载地址: https://ffmpeg.org/download.html
    *   解压后将 `bin` 目录添加到环境变量 Path 中。

## 使用方法

### 1. 基础扫描

扫描 D 盘和网络共享，并生成报告：

```bash
python movie_manager/main.py --paths "D:\Movies" "\\192.168.1.100\Public\Video"
```

运行后会在当前目录生成 `movie_report.html`。

### 2. 启用 Web 服务（推荐）

添加 `--serve` 参数，程序会在生成报告后自动启动 Web 服务，允许你在浏览器中直接**打开文件**或**定位文件**。

```bash
python movie_manager/main.py --paths "D:\Movies" --serve
```

### 3. 启用在线搜索（跨语言匹配）

如果你有 TMDB API Key（免费申请），可以开启在线别名搜索功能，极大提高中英文文件名的匹配准确率：

```bash
python movie_manager/main.py --paths "D:\Movies" --tmdb-key "你的_TMDB_API_KEY"
```

## 高级筛选规则 (HTML 报告)

在生成的 HTML 报告中，你可以使用 JavaScript 表达式进行复杂筛选：

*   **查找大于 1GB 的 MKV 文件**:
    ```javascript
    (size_mb > 1024) & (extension == '.mkv')
    ```

*   **查找路径包含 "Sci-Fi" 且时长大于 2 小时**:
    ```javascript
    (path.includes('Sci-Fi')) & (duration > 7200)
    ```

*   **查找特定番号**:
    ```javascript
    id_code == 'RBC-1007'
    ```

## 注意事项

*   **性能**: 程序会尝试读取每个视频文件的头部信息以获取时长。对于网络共享文件，这可能需要一些时间，取决于网络速度。
*   **安全**: `--serve` 模式启动的 Web 服务器仅监听本地请求，但请勿在公共网络环境下暴露该端口。

## EXE/ZIP 打包说明（build.py）

已审阅项目根目录的 [build.py](file:///c:/Users/Player_X/Documents/Tare/duplicate_media_checker/build.py)，当前**已经实现可用打包功能**，无需额外补写打包脚本。

### 打包脚本已实现的功能

1. 自动读取版本号  
   从 [web_server.py](file:///c:/Users/Player_X/Documents/Tare/duplicate_media_checker/movie_manager/web_server.py) 的 `__version__` 读取版本。
2. 生成 `version_info.txt`  
   用于写入 Windows 可执行文件版本信息。
3. 调用 PyInstaller 生成单文件 EXE  
   实际调用方式是 `sys.executable -m PyInstaller`，避免 PATH 问题。
4. 自动生成 ZIP 发布包  
   将 `dist` 下的 exe、`ff_res` 目录文件和 `README_DEPLOY.md`（重命名为 `README.md`）打进 zip。

### 打包前准备

在项目根目录执行：

```powershell
pip install pyinstaller
```

确保以下内容存在：

- `movie_manager/templates`（打包参数会包含该目录）
- `ff_res`（建议包含 `ffmpeg.exe`、`ffprobe.exe`，用于发布后可直接运行）

### 一键打包命令

```powershell
python build.py
```

### 产物位置与命名

执行成功后会在 `dist` 目录生成：

- `DuplicateChecker_v<version>.exe`
- `DuplicateChecker_v<version>.zip`

其中 `<version>` 来自 `movie_manager/web_server.py` 的 `__version__`。

### 发布使用方式

1. 将 zip 发给目标机器  
2. 解压到任意目录  
3. 双击 `DuplicateChecker_v<version>.exe` 启动

### 常见问题

- 若提示找不到 PyInstaller：先执行 `pip install pyinstaller`，并确认当前 Python 环境与运行 `python build.py` 的环境一致。
- 若启动后媒体分析不可用：检查发布目录中是否包含 `ffmpeg.exe` 和 `ffprobe.exe`（通常来自 `ff_res`）。
