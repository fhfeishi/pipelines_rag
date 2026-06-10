# pipelines-transcript

音视频转文案精品工具：**听写 → 润色 → 标题 → 可直接发布的正文**。

## 快速开始

```bash
uv sync

# 最简单：一条命令
uv run transcript /path/to/audio.m4a

# 推荐中文口播
uv run transcript /mnt/d/download/resources/proxy-tun.m4a
```

配置 `configs/.env`（或环境变量）：

- `dashscope_api_key` — 听写
- `deepseek_api_key` — 润色 + 标题

系统需安装 `ffmpeg`。

## 常用命令

```bash
transcript audio.m4a                    # 默认中文润色+标题
transcript audio.m4a -o tmp/outs      # 指定输出目录
transcript audio.m4a -l en            # 英文
transcript audio.m4a --no-polish      # 仅听写
transcript audio.m4a -f txt,json,srt  # 额外字幕
transcript --batch "raws/*.m4a" -o outs --flat
```

也支持：`uv run -m video_transcript.cli ...`（等价）

## 输出（以 `proxy-tun.m4a` 为例）

```text
tmp/outs/proxy-tun/
  proxy-tun.transcript.txt      # ✅ 交付物：标题 + 正文
  proxy-tun.title.txt           # 单独标题
  proxy-tun.raw.transcript.txt  # ASR 原文
  proxy-tun.transcript.json     # 全量元数据
  proxy-tun.summary.json        # 运行摘要
```

`transcript.txt` 格式：

```text
系统代理与 TUN 模式的区别

为什么你明明开了代理，浏览器能用……
（润色后的分段正文）
```

## 支持格式

音频：mp3, m4a, wav, flac, aac, ogg, opus, wma …  
视频：mp4, mkv, mov, avi, webm, flv, ts …（自动无损抽音轨）

```bash
# 仅测试抽轨/转码，不调用 API
uv run transcript video.mp4 --prepare-only
```

## Pipeline

```text
音频/视频 → ffprobe → 无损抽轨(copy) → 16k mono WAV → DashScope ASR → DeepSeek 润色+标题
```

## 省钱

优先传 **m4a/mp3**（比 mp4 小，不传画面）。视频会自动 `-c:a copy` 抽音轨，只在 ASR 前降采样。默认缓存。
