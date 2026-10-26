# 部署指南 (Deployment Guide)

本指南介绍如何将 `duplicate_media_checker` 打包为独立的可执行文件 (.exe)，以便在**未安装 Python** 的其他 Windows 电脑上运行。

## 1. 准备工作

在开发机（已安装 Python 的电脑）上，确保已安装打包工具 `pyinstaller`：

```powershell
pip install pyinstaller
```

## 2. 打包程序

使用提供的构建脚本 `build.py` 进行打包。该脚本会自动读取 `web_server.py` 中的版本号，生成版本信息文件，并调用 PyInstaller。

在项目根目录运行：

```powershell
python build.py
```

## 3. 获取结果

打包完成后，您会在 `dist` 文件夹中找到生成的 `.zip` 压缩包（例如 `DuplicateChecker_v0.0.4.zip`）。该压缩包已包含所有必要文件。

## 注意：画质对比依赖（可选）

打包的 exe 不包含 `videodiff`。若需要画质对比功能，需在目标机器上：

1. 安装 Python 3.10+ 与 video_diff 项目（`pip install <video_diff 路径>`），并确保 `videodiff` 命令可用（在 PATH 中，或设置 `VIDEODIFF` 环境变量）；
2. 保证 ffmpeg 带 ssim/psnr 滤镜（`ff_res` 内的 ffmpeg 已具备；VMAF 需完整版 ffmpeg）。

未安装时其余功能（扫描、去重、版本聚类、批量清理）完全可用。

## 4. 在其他电脑上运行

要将程序部署到其他电脑（**无需安装 Python**），请遵循以下步骤：

1.  **解压压缩包**：将生成的 `.zip` 文件复制到目标电脑并解压到一个文件夹中。
2.  **运行**：双击解压后的 `.exe` 文件（例如 `DuplicateChecker_v0.0.4.exe`）即可启动。
    *   程序会自动打开浏览器界面。
    *   **注意**：压缩包内已包含 `ffmpeg.exe` 和 `ffprobe.exe` 等依赖文件，**无需额外下载**。
    *   **日志文件**：程序运行后会在同级目录下生成 `duplicate_checker.log`，如果遇到问题可以查看此文件。

## 5. 目录结构示例

最终部署文件夹的结构应如下所示：

```text
DuplicateChecker/
├── DuplicateChecker_v1.0.0.exe  (主程序)
├── duplicate_checker.log        (运行时自动生成)
├── ffmpeg.exe                   (必须)
├── ffprobe.exe                  (必须)
└── exclusions.json              (可选，如果之前有保存过的排除列表)
```

## 6. 常见问题

*   **杀毒软件误报**：由于 PyInstaller 的工作原理，某些杀毒软件（如 Windows Defender）可能会将未签名的 `.exe` 标记为可疑。这是正常现象，请将其添加到信任列表。
*   **权限问题**：如果需要扫描系统受保护的目录（如 C 盘根目录），建议右键选择“以管理员身份运行”。
*   **浏览器未自动打开**：如果双击后浏览器没有自动弹出，请手动打开浏览器访问 `http://127.0.0.1:5000`。
