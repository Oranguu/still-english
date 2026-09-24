# Still · 慢慢听

把英文视频变成可反复练习的本地教材。完整看一遍，再逐句精听，按自己的节奏打开字幕和讲解。

Still 是一个在本机浏览器中使用的英文学习工具。输入 YouTube 或 Bilibili 视频链接，或导入已有素材包，即可练习。**仓库只提供工具源码，不附带视频、字幕、AI 教材或个人学习记录；首次启动时素材库为空。**

## 可以做什么

- **完整观看**：无字幕、英文字幕、中英对照三种模式。
- **逐句精听**：点击语句跳转，句末自动暂停，支持循环和调整速度。
- **先听后看**：无字幕模式同时隐藏语句原文，可主动揭晓答案。
- **词句讲解**：中文释义、词汇短语、语法说明、常见弱读与连读提示、英文改写及练习。
- **听写与复习**：听写对照、收藏、已掌握标记、个人笔记和学习位置保存。
- **可迁移素材包**：视频、字幕时间轴和生成的讲解保存在同一个文件夹，准备完成后可离线学习。

片段使用时间索引定位原视频，不重复保存大量小视频文件。

## 开始使用

目前在 **macOS** 上验证过安装和学习流程。Windows、Linux 尚未完成端到端验证；双击启动脚本仅适用于 macOS。

### 1. 下载源码

从 [GitHub 仓库](https://github.com/Oranguu/still-english) 选择 **Code → Download ZIP** 并解压，或使用 Git：

```sh
git clone https://github.com/Oranguu/still-english.git
cd still-english
```

需要 Python **3.10 或更新版本**，推荐 3.12。也支持已安装的 [uv](https://docs.astral.sh/uv/) 创建运行环境。安装依赖和首次准备素材需要网络连接。

### 2. 安装并启动

在项目根目录运行：

```sh
python3 tool/setup.py
tool/.venv/bin/python tool/launcher.py
```

macOS 也可以双击 `tool/安装依赖.command`，安装完成后双击 `tool/启动.command`。请保留启动的终端窗口；关闭窗口会停止服务。

工具会打开 [本地学习页](http://127.0.0.1:8765)。如果端口已经被其他程序占用，请先关闭占用它的程序。

### 3. 准备素材并学习

1. 选择「新建素材包」，粘贴单个 YouTube 或 Bilibili 视频链接。
2. 选择是否生成 AI 讲解。未配置 Codex 时，可以先关闭自动生成，准备有英文字幕的素材。
3. 等待视频下载、字幕整理和可选的 AI 处理。完成的素材包保存在项目根目录，与 `tool` 并列。
4. 打开素材卡片开始学习。也可以拖入完整素材包文件夹，或使用文件夹选择按钮导入。

快捷键：空格播放或暂停，左右箭头切换语句，`R` 重听，`L` 循环，`C` 切换字幕。输入框中不会触发这些快捷键。

## AI 讲解：使用自己的 Codex 账号

Still 调用本机安装的官方 **Codex CLI**，并要求使用自己的 ChatGPT 账号登录。请按 [Codex 官方文档](https://learn.chatgpt.com/docs/auth) 安装和登录，然后运行：

```sh
codex login
codex login status
```

- 生成讲解使用该账号的 **Codex 用量**，受账号可用模型和额度限制。它不是 ChatGPT 网页的通用 API，也不保证与所有聊天模式共享同一限额。
- 源码不附带账号或 API 密钥。工具要求 ChatGPT 登录方式，并从 AI 子进程环境中移除 OpenAI API 密钥变量。
- 生成时会把视频标题、英文字幕及相邻语句上下文发送给 Codex；不会把整部视频作为讲解输入上传。
- 每批完成后保存结果。遇到错误或用量限制，可以稍后点击「补全 AI 讲解」，继续缺失部分。
- 已经完成的素材包可以离线播放和练习，无需登录 Codex。生成新讲解时需要联网及可用的 Codex 登录。

讲解以约 13 岁学生能够理解的中文表达为目标。字幕和 AI 内容可能出错；弱读、连读提示基于文本中的常见规律，**不是对原视频实际发音的听音鉴定**。

相关文档：[Codex 非交互运行](https://learn.chatgpt.com/docs/non-interactive-mode)。Codex CLI 的参数和服务可用性可能随版本变化；若生成失败，请先核对官方文档及当前登录状态。

## 没有英文字幕时

工具优先使用人工英文字幕，其次使用自动英文字幕。没有可用英文字幕时，若已安装本地转写组件，会自动尝试识别英文语音，即使关闭了自动 AI 讲解。若组件尚未安装，视频仍会保留，并提示补充字幕。可以通过以下方式继续：

1. 在学习页导入与视频时间一致的英文 `.srt`、`.vtt` 或 `.json3` 字幕。
2. 安装本地英语转写组件：

   ```sh
   python3 tool/setup.py --transcription
   ```

   macOS 也可以双击 `tool/安装语音转写.command`，再回到学习页点击识别语音并生成讲解。

首次转写会下载 Whisper `base.en` 模型，之后在本机识别英文音频，不调用付费语音 API。模型位于 `tool/.state/models/`；口音、音乐和录音质量会影响结果。该流程用于英文原声，不会把中文原声变成英文听力材料。

## 素材包和个人数据

```text
still-english/
├── README.md
├── LICENSE
├── tool/
│   ├── server.py
│   ├── static/
│   ├── .venv/                 # 本机依赖，不进入 Git
│   └── .state/                # 日志、模型、任务及个人进度，不进入 Git
└── 视频标题--视频ID/           # 自行准备的素材包，不进入 Git
    ├── manifest.json          # 视频信息、时间轴、翻译和讲解
    ├── video.mp4
    ├── poster.jpg
    ├── original.json3         # 原字幕，格式可能不同，也可能没有
    ├── english.vtt
    └── bilingual.vtt
```

复制完整素材包文件夹即可迁移教材。收藏、笔记、听写和学习位置另外保存在 `tool/.state/progress/`，需要时单独备份。相同视频 ID 的素材包会打开已有版本，不覆盖现有教材。

工具默认不读取浏览器登录信息。只有在下载高级选项中明确选择浏览器后，下载器才会读取该浏览器的登录信息用于下载。运行日志、缓存及下载元数据可能包含私人字幕、本机路径或临时链接；不要直接公开这些文件。详见 [隐私与安全说明](SECURITY.md)。

**`.gitignore` 不会过滤普通文件夹压缩。** 分享工具源码时，优先使用 GitHub 的 **Download ZIP**。如果从本地导出，先检查提交内容，再仅导出 Git 已提交的文件：

```sh
python3 tool/scripts/check_public_files.py --tree HEAD
git archive --format=zip --output=still-english-source.zip HEAD
```

这份源码压缩包不包含被 Git 忽略的素材、运行记录或本机依赖。导出前的检查通过并不代表任何内容都适合公开，仍应审阅本次提交。

## 下载与使用范围

- 下载使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，视频处理使用 FFmpeg。视频平台的规则、下载器版本、网络和资源权限会影响能否下载。
- YouTube 已验证基本流程；Bilibili 接入同一下载器，尚未完成实际视频的端到端验证。不同视频的字幕可用性不同。
- 遇到平台登录验证时，可在高级选项选择已登录的浏览器；这一选择不保证所有受限资源都能下载。
- YouTube 下载可能需要当前 yt-dlp 支持的 JavaScript 运行时。工具会使用可用的 Node.js；具体要求以 [yt-dlp 文档](https://github.com/yt-dlp/yt-dlp#dependencies) 为准。
- 请仅下载、处理和分享你有权使用的内容。项目的开源许可适用于工具源码，不授予第三方视频、字幕或其他素材的权利。

## 开发与贡献

服务端为 Python，前端为静态 HTML/CSS/JavaScript，无前端构建步骤。测试使用临时合成数据，不需要真实素材、Codex 登录或第三方 Python 依赖：

```sh
python3 -m unittest discover -s tool/tests -v
```

提交前检查实际暂存内容：

```sh
python3 tool/scripts/check_public_files.py
```

仓库通过文件白名单限制可提交内容。新增公开文件时，需要同步更新白名单和检查器，并人工审阅。具体步骤见 [贡献指南](CONTRIBUTING.md)。

本地服务仅绑定 `127.0.0.1`，适用于个人电脑，不是可直接部署到公网的多人服务。

## 许可

工具源码采用 [MIT License](LICENSE)。第三方依赖、模型和自行导入的素材分别遵循其各自许可。
