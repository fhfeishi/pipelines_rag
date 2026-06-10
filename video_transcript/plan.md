# Video Transcript Tool — Plan

## 标准 Pipeline ✅

```text
input (audio|video, common formats)
  -> ffprobe probe
  -> lossless extract (stream copy / FLAC fallback)
  -> ASR normalize (mono 16kHz WAV)
  -> DashScope qwen3-asr-flash
  -> DeepSeek polish + title (JSON, default on)
  -> {stem}.transcript.txt   # title + body
     {stem}.title.txt
     {stem}.raw.transcript.txt
     {stem}.transcript.json
     {stem}.summary.json
```

## CLI 设计原则 ✅

- 命令名：`transcript`（`uv run transcript file.m4a`）
- 短参数：`-o` 输出目录，`-l` 语言，`-f` 格式
- `--prepare-only` 仅测媒体转换，不调用 API
- 默认 `-l zh`，默认润色+标题，默认缓存
- Python API：`video_transcript.pipeline.run(...)`

## 已完成阶段

| Phase | 内容 |
|-------|------|
| 0 | 清理 RAG 遗留 |
| 1 | DashScope ASR MVP |
| 2 | 命名规范 + API 元数据 |
| 3 | 润色 pipeline |
| 4 | 标题生成 + CLI 精品化 + `pipeline.run()` |

## Next

- [ ] 逐段润色回写 SRT
- [ ] `fun-asr` 超长音频异步路径
- [ ] 本地 Whisper（暂缓）

## Validation

```bash
uv run transcript --help
uv run transcript /mnt/d/download/resources/proxy-tun.m4a --force
```

检查：`transcript.txt` 首行为标题，正文可直接发布；`summary.json` 含 `title`。
