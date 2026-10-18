# 燕学工作台

面向北京大学学生的本机学习工具：从教学网课程录播、课件和转录稿生成可追溯的 Markdown 笔记。

## 下载与启动

在下载页面获取 `yanxue-windows.zip`，解压到普通文件夹，首次双击 `一键安装并启动.cmd`。安装完成后，日常双击 `打开可视化界面.cmd`。

需要 Windows、Python 3.11 或更新版本，以及已安装并加入 PATH 的 FFmpeg/FFprobe。首次安装需要联网下载 Python 依赖和 Chromium。

首次启动时选择已有 PKU-Recorder 的项目目录。该目录应包含 `pku_recorder_app/app.py`；也可直接选择包含 `app.py` 的目录。下载器需使用与本工具适配的 Web API。PKU-Recorder 是独立安装的工具，不包含在本发布包内。

如果取消选择下载器，仍可导入本地视频、音频、PDF、PPTX、DOCX、TXT 和 Markdown。

正式 AI 生成需要在 Windows 用户环境变量中配置 `OPENAI_API_KEY`；可选配置 `OPENAI_BASE_URL`。设置后重新打开工作台。没有有效密钥时可用“只提取证据”。转录、笔记和审计会调用配置的 AI 服务，并按服务商规则计费。

## 一键处理

在工作台启动教学网连接，使用自己的账号登录，选择课程、录播和笔记类型，点击“一键生成学习资料”。后台依次下载录播、汇集课件、转录音频、整理笔记、输出质检报告。

笔记支持经济学、理工科、人文社科、法学、语言和通用课程。初稿及质检报告可在同一页面查看；初稿可修改、保存和定稿。当前阅读器显示 Markdown 源文。

## 数据与配置

课程区保存在 `workspaces/`。账号密码不写入笔记、配置或日志，只转发给本机下载后台。教学网会话由原有下载器管理。工作台默认仅监听 `127.0.0.1:8765`。

课程文件在本机保存；选择正式 AI 生成后，音频和提取的课程内容会发送给你配置的 AI 服务。请按课程材料的使用要求处理和分享笔记。

自定义提示词位于课程区的 `prompt.md`，模型与术语配置位于 `course.toml`。页面中的“课程工作区”显示当前数据位置。

## 当前边界

本版本是可用的源码发布版，需要 Python 运行环境；不是免安装 EXE。教学网登录和正式 AI 生成需使用者提供有效账号及 API Key。课件与录播尚未实现精确逐页视觉对齐，当前按页码文本与分段转录共同生成笔记。

## 开发

```powershell
python -m pip install ".[documents,recorder]"
python -m playwright install chromium
python -m unittest discover -s tests -v
python -m course_notes.launcher
```
