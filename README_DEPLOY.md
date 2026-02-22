# 部署指南 (Deployment Guide)

本指南介绍如何将 `duplicate_media_checker` 打包为独立的可执行文件 (.exe)，以便在**未安装 Python** 的其他 Windows 电脑上运行。

## 1. 准备工作

在开发机（已安装 Python 的电脑）上，确保已安装打包工具 `pyinstaller`：

```powershell
pip install pyinstaller
```

## 2. 打包程序

在项目根目录（即 `movie_manager` 文件夹所在的上一级目录）打开终端，运行以下命令：

```powershell
pyinstaller --noconfirm --onefile --windowed --name "DuplicateChecker" --add-data "movie_manager/templates;movie_manager/templates" movie_manager/web_server.py
```

**参数说明：**
*   `--onefile`: 将所有依赖（Python解释器、库文件）打包进一个单独的 `.exe` 文件。
*   `--windowed`: 运行时不显示黑色的命令行窗口（如果是调试阶段可以去掉此参数）。
*   `--name "DuplicateChecker"`: 指定生成的可执行文件名称。
*   `--add-data ...`: **[重要]** 将 HTML 模板文件打包进去，否则程序运行会报错找不到页面。

## 3. 获取结果

打包完成后，您会在项目目录下的 `dist` 文件夹中找到 `DuplicateChecker.exe`。

## 4. 在其他电脑上运行

要将程序部署到其他电脑（**无需安装 Python**），请遵循以下步骤：

1.  **创建文件夹**：在目标电脑上新建一个文件夹（例如 `DuplicateChecker`）。
2.  **复制主程序**：将 `dist/DuplicateChecker.exe` 复制到该文件夹。
3.  **复制 FFmpeg (关键)**：
    *   本程序依赖 `ffmpeg.exe` 和 `ffprobe.exe` 来处理视频。打包程序**不包含**这两个文件。
    *   请下载 Windows 版的 FFmpeg (Builds by BtbN 或 Gyan.dev)。
    *   解压并将 `bin` 目录下的 `ffmpeg.exe` 和 `ffprobe.exe` 复制到与 `DuplicateChecker.exe` **相同的文件夹**中。
4.  **运行**：双击 `DuplicateChecker.exe` 即可启动。程序会自动打开浏览器界面。

## 5. 目录结构示例

最终部署文件夹的结构应如下所示：

```text
DuplicateChecker/
├── DuplicateChecker.exe  (主程序)
├── ffmpeg.exe            (必须)
├── ffprobe.exe           (必须)
└── exclusions.json       (可选，如果之前有保存过的排除列表)
```

## 6. 常见问题

*   **杀毒软件误报**：由于 PyInstaller 的工作原理，某些杀毒软件（如 Windows Defender）可能会将未签名的 `.exe` 标记为可疑。这是正常现象，请将其添加到信任列表。
*   **权限问题**：如果需要扫描系统受保护的目录（如 C 盘根目录），建议右键选择“以管理员身份运行”。
*   **浏览器未自动打开**：如果双击后浏览器没有自动弹出，请手动打开浏览器访问 `http://127.0.0.1:5000`。
