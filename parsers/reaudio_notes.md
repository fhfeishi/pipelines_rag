# reaudio_dashscope.py 开发手册

`reaudio_dashscope.py` 是 `parsers/` 下的音视频结构化解析入口。目标是把本地音频、本地视频、公开视频链接统一归一成音频，再用 DashScope ASR 转写，最后输出可进入知识库或人工整理的 Markdown / JSON / TXT / SRT。

```text
local audio/video OR video URL
  -> yt-dlp download when input is URL
  -> ffmpeg normalize to 16 kHz mono wav
  -> DashScope ASR
  -> optional DashScope LLM polish
  -> md / json / txt / srt
```

## 文件位置

```bash
parsers/reaudio_dashscope.py
parsers/reaudio_notes.md
```

推荐从仓库根目录运行：

```bash
.venv/bin/python -m parsers.reaudio_dashscope --help
```

## 依赖

必需：

| 依赖 | 作用 | 检查方式 |
|------|------|----------|
| `ffmpeg` | 从本地视频/下载媒体中抽音频，并转 16 kHz mono wav | `command -v ffmpeg` |
| `dashscope` Python SDK | DashScope ASR 与可选 LLM 润色 | `.venv/bin/python -c "import dashscope"` |
| DashScope API key | 调用 ASR/LLM | 环境变量或 `configs/.env` |

URL 输入额外需要：

| 依赖 | 作用 | 检查方式 |
|------|------|----------|
| `yt-dlp` | 下载 YouTube / Bilibili / 通用视频链接音频 | `.venv/bin/python -m yt_dlp --version` |

本机已验证安装：

```bash
.venv/bin/python -m pip install yt-dlp
```

## API Key

默认参数：

```bash
--api-key-env DASHSCOPE_API_KEY
```

读取顺序：

1. 环境变量 `DASHSCOPE_API_KEY`
2. `configs/.env` 中同名键
3. 当使用默认 key 名时，也读取 `configs/.env` 中的 `dashscope_api_key`

示例 `configs/.env`：

```dotenv
dashscope_api_key=sk-...
```

## 基础用法

### 本地音频

```bash
.venv/bin/python -m parsers.reaudio_dashscope inputs/audio/demo.mp3
```

### 本地视频

视频会先走 `ffmpeg` 抽音频：

```bash
.venv/bin/python -m parsers.reaudio_dashscope inputs/videos/demo.mp4
```

### YouTube 链接

URL 输入会先走 `yt-dlp` 下载 bestaudio，然后复用同一条 ASR pipeline：

```bash
.venv/bin/python -m parsers.reaudio_dashscope \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Bilibili 链接

Bilibili 经常需要登录态 cookie；脚本已经支持两种方式：

```bash
# 从浏览器读取 cookie
.venv/bin/python -m parsers.reaudio_dashscope \
  "https://www.bilibili.com/video/BV..." \
  --cookies-from-browser chrome

# 或传 cookies.txt
.venv/bin/python -m parsers.reaudio_dashscope \
  "https://www.bilibili.com/video/BV..." \
  --cookies cookies.txt
```

脚本会对 `bilibili.com` 自动传 `--referer https://www.bilibili.com/`。如果仍遇到 HTTP 412，通常需要 cookie。

### 只处理前 N 秒

用于快速 smoke test，避免长视频消耗太多 ASR 额度：

```bash
.venv/bin/python -m parsers.reaudio_dashscope video.mp4 --max-seconds 60
```

### 跳过 LLM 润色

只做 ASR：

```bash
.venv/bin/python -m parsers.reaudio_dashscope video.mp4 --no-polish
```

### Dry-run：只检查接口和计划，不消耗 API

用于先验证本地文件、依赖、输出路径和 DashScope key 状态：

```bash
.venv/bin/python -m parsers.reaudio_dashscope \
  /mnt/e/static_docs/sources/proxy-tun.m4a \
  --output-dir outputs/static_docs/proxy-tun/reaudio_dashscope \
  --formats md,json,srt \
  --max-seconds 30 \
  --no-polish \
  --dry-run
```

`--dry-run` 不会执行 ffmpeg、不会下载 URL、不会调用 DashScope ASR/LLM，也不会写输出文件。它会打印 JSON plan，包括：

- 输入文件是否存在、大小、解析后的路径。
- 将要写出的 `md/json/srt/txt` 路径。
- pipeline 步骤、`max_seconds`、语言提示、缓存策略。
- `ffmpeg` / `yt-dlp` 可执行文件位置。
- `DASHSCOPE_API_KEY` 是否可用。

### 输出格式

默认：

```bash
--formats md,json
```

可选：

```bash
--formats md,json,txt,srt
```

## URL 下载参数

| 参数 | 说明 |
|------|------|
| `--yt-dlp-binary` | `yt-dlp` 可执行文件名；如果命令不存在，会 fallback 到 `.venv/bin/python -m yt_dlp` |
| `--yt-dlp-format` | 传给 `yt-dlp -f`，默认 `bestaudio/best` |
| `--http-proxy` | 传给 yt-dlp 的代理，默认取环境变量，兜底 `http://127.0.0.1:7897` |
| `--cookies` | cookie 文件 |
| `--cookies-from-browser` | 从浏览器读 cookie，例如 `chrome` / `firefox` |
| `--referer` | 自定义 referer |
| `--user-agent` | 自定义 User-Agent |
| `--url-download-timeout` | yt-dlp socket timeout，默认 60 秒 |
| `--keep-source-media` | 保留 URL 下载到的源媒体文件 |
| `--download-dir` | 源媒体保留目录，默认 `<output-dir>/media` |

## 输出文件

默认输出目录是 `outputs/`，可以用 `--output-dir` 改：

```bash
.venv/bin/python -m parsers.reaudio_dashscope video.mp4 --output-dir outputs/reaudio
```

常见产物：

| 文件 | 说明 |
|------|------|
| `<stem>.md` | 带时间戳段落的 Markdown |
| `<stem>.json` | 结构化转写结果，含原始输入、解析路径、URL 元数据、segments |
| `<stem>.txt` | 简单文本转写 |
| `<stem>.srt` | 字幕格式 |
| `<stem>.16k.wav` | 传 `--keep-wav` 时保留的标准化音频 |
| `.cache/*.json` | ASR 缓存 |
| `media/*` | 传 `--keep-source-media` 时保留的 URL 下载源文件 |

Markdown 头部会记录：

- 原始输入：本地路径或 URL
- 解析后的媒体路径
- 来源说明：local file / url via yt-dlp
- ASR/润色模型
- 视频 id（URL 输入且 yt-dlp 可取到时）
- 生成时间

JSON 结构大致为：

```json
{
  "source": "asr:dashscope:paraformer-realtime-v2+polish:qwen-plus",
  "input": {
    "original": "https://...",
    "resolved_path": "/tmp/...",
    "source_note": "url via yt-dlp: https://...",
    "metadata": {
      "url": "https://...",
      "downloaded_path": "/tmp/...",
      "title": "...",
      "video_id": "..."
    }
  },
  "segments": [
    {"start": 0.0, "end": 3.2, "text": "..."}
  ]
}
```

## 缓存策略

默认开启 ASR 缓存：

- 本地文件：基于文件内容 hash、ASR 模型、语言提示、截取时长
- URL：基于原始 URL、ASR 模型、语言提示、截取时长

参数：

| 参数 | 说明 |
|------|------|
| `--no-cache` | 禁用缓存 |
| `--force` | 忽略已有缓存，重新转写 |

注意：URL 输入如果缓存命中，可以避免重复 ASR；但当前实现仍会先下载 URL 以解析输入。后续可优化为先根据 URL 预测 cache path，再决定是否跳过下载。

## 当前验证记录（2026-07-01）

已通过：

```bash
.venv/bin/python -m parsers.reaudio_dashscope --help
.venv/bin/python -m compileall parsers/reaudio_dashscope.py
```

本地文件 smoke test：

```bash
ffmpeg -y -v error -f lavfi -i anullsrc=r=16000:cl=mono -t 1 outputs/test_audio/silence.wav
.venv/bin/python -m parsers.reaudio_dashscope \
  outputs/test_audio/silence.wav \
  --no-polish \
  --api-key-env DEFINITELY_MISSING_DASHSCOPE_KEY \
  --max-seconds 1
```

结果：能正确进入本地文件解析/ffmpeg 路径，并在缺少指定 key 时友好报错。

YouTube URL smoke test：

```bash
.venv/bin/python -m parsers.reaudio_dashscope \
  "https://www.youtube.com/watch?v=jNQXAC9IVRw" \
  --no-polish \
  --api-key-env DEFINITELY_MISSING_DASHSCOPE_KEY \
  --max-seconds 1 \
  --url-download-timeout 30
```

结果：`yt-dlp -> ffmpeg` 前半段通过，随后按预期在缺少指定 DashScope key 时停止，避免消耗 ASR 额度。

Bilibili URL smoke test：

```bash
.venv/bin/python -m parsers.reaudio_dashscope \
  "https://www.bilibili.com/video/BV1GJ411x7h7" \
  --no-polish \
  --api-key-env DEFINITELY_MISSING_DASHSCOPE_KEY \
  --max-seconds 1 \
  --url-download-timeout 30
```

结果：当前网络/站点状态下，Bilibili 返回 HTTP 412。已自动加 referer，但仍需要 `--cookies` 或 `--cookies-from-browser`。

## `/mnt/e/static_docs` 本地音视频接口 smoke（2026-07-02）

目标：先跑通 `reaudio_dashscope.py` 的命令行接口、输入识别和输出规划，不批量转写、不消耗 DashScope 额度。

环境检查：

| dependency | result |
|------------|--------|
| `ffmpeg` | `/usr/bin/ffmpeg` |
| `DASHSCOPE_API_KEY` | 当前 dry-run 显示不可用 |

### m4a dry-run

输入：

```text
/mnt/e/static_docs/sources/proxy-tun.m4a
```

命令：

```bash
wsl -d Ubuntu-22.04 --cd /home/baheas/wslcodespace/pipelines_rag \
  /home/baheas/.local/bin/uv run -m parsers.reaudio_dashscope \
  /mnt/e/static_docs/sources/proxy-tun.m4a \
  --output-dir outputs/static_docs/proxy-tun/reaudio_dashscope \
  --formats md,json,srt \
  --max-seconds 30 \
  --no-polish \
  --dry-run
```

结果：通过。识别为本地文件，大小 `3135933` bytes，规划输出：

```text
outputs/static_docs/proxy-tun/reaudio_dashscope/proxy-tun.md
outputs/static_docs/proxy-tun/reaudio_dashscope/proxy-tun.json
outputs/static_docs/proxy-tun/reaudio_dashscope/proxy-tun.srt
```

### mp4 dry-run

输入：

```text
/mnt/e/static_docs/sources/rag-or-not.mp4
```

命令：

```bash
wsl -d Ubuntu-22.04 --cd /home/baheas/wslcodespace/pipelines_rag \
  /home/baheas/.local/bin/uv run -m parsers.reaudio_dashscope \
  /mnt/e/static_docs/sources/rag-or-not.mp4 \
  --output-dir outputs/static_docs/rag-or-not/reaudio_dashscope \
  --formats md,json \
  --max-seconds 15 \
  --no-polish \
  --dry-run
```

结果：通过。识别为本地文件，大小 `13331564` bytes，规划为 `ffmpeg -> 16 kHz mono wav -> DashScope ASR -> write md/json`。

### 当前结论

- `reaudio_dashscope.py --dry-run` 可作为默认接口 smoke，不消耗 API。
- 当前机器缺少 `DASHSCOPE_API_KEY`，真实 ASR 需要先在环境变量或 `configs/.env` 中配置 `dashscope_api_key`。
- 有 key 后建议先跑 `--max-seconds 30 --no-polish`，确认 ASR 输出结构；再打开 polish。

## 常见问题

### zsh 报 `no matches found`

URL 中有 `?` 时，zsh 可能把它当 glob。给 URL 加引号：

```bash
.venv/bin/python -m parsers.reaudio_dashscope "https://www.youtube.com/watch?v=..."
```

### Bilibili HTTP 412

通常是风控/登录态问题。使用：

```bash
--cookies-from-browser chrome
```

或导出 cookies.txt 后：

```bash
--cookies cookies.txt
```

### YouTube / 外网下载慢或超时

本机默认代理：

```bash
--http-proxy http://127.0.0.1:7897
```

如当前 shell 已有代理环境变量，会优先使用环境变量。

### DashScope key 未找到

确认：

```bash
echo $DASHSCOPE_API_KEY
```

或写入：

```dotenv
dashscope_api_key=sk-...
```

## 待优化

P0 / 正确性：

- 增加真实 DashScope ASR 端到端回归样例，覆盖短音频、本地视频、YouTube URL。
- 对 DashScope `RecognitionResult` 的异常/空返回做更细粒度的错误信息，不再只依赖兼容解析。
- Bilibili cookie 流程补充实测样例，确认 `--cookies-from-browser chrome` 在当前 WSL/Windows 环境是否能读到 cookie。

P1 / 工程体验：

- URL cache 命中时跳过下载：当前为了拿标题/stem 会先下载，后续可先用 URL 生成 cache key，命中后直接写输出。
- 增加 `--download-only` / `--extract-audio-only`，方便调试下载与音频抽取，不调用 DashScope。
- ~~增加 `--dry-run`，打印解析计划、依赖检查、输出路径、是否会命中 cache。~~ 第一版已加：当前显示 cache 策略但不计算真实 cache key，避免 dry-run 对本地大文件做 hash 或下载 URL。
- 将默认输出目录从通用 `outputs/` 调整为 `outputs/reaudio/`，避免和网页/PDF parser 产物混在一起。
- 对超长音频增加切片 ASR，避免单次请求大小或时长限制。

P2 / 数据结构：

- 输出 `manifest.json`，统一记录输入、依赖版本、命令参数、模型版本、耗时、文件路径。
- JSON segments 增加 `speaker`、`confidence`、`words` 字段的兼容位置，便于以后接说话人分离或词级时间戳。
- Markdown 支持按章节/静音间隔自动分组，而不是只按 ASR segment 平铺。

P3 / 多后端：

- 抽象 ASR backend，后续可接 Whisper / FunASR / 本地模型。
- 抽象 polish backend，支持 DeepSeek/OpenAI-compatible，而不只 DashScope `Generation`。
- 对 URL 平台增加专门策略：YouTube 字幕优先、Bilibili 字幕/弹幕可选、播客 RSS 自动取 enclosure。
