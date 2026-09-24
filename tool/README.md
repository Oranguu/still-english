# Still · 慢慢听

完整安装说明、功能介绍和素材包格式见 [项目 README](../README.md)。

在项目根目录安装并启动：

```sh
python3 tool/setup.py
tool/.venv/bin/python tool/launcher.py
```

需要 Python 3.10+，推荐 3.12。macOS 也可以依次双击本目录的 `安装依赖.command` 和 `启动.command`。

第一次启动时素材库为空。自行准备的素材包保存在项目根目录；个人进度和日志位于本目录的 `.state/`，本机依赖位于 `.venv/`。这些文件不属于公开源码。

AI 讲解需要用户自己的 Codex CLI 和 ChatGPT 登录，使用自己的 Codex 用量。已有素材包可离线学习。

分享源码请使用 GitHub Download ZIP 或根目录 README 中的导出方法，不要直接压缩包含个人数据的工作目录。
