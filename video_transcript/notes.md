# Notes — Video Transcript Pipeline

背景研究与实践备忘。活跃计划见 `plan.md`，运行记录见 `logs.md`。

## 目标体验

用户拿到 `{stem}.transcript.txt` 就能直接发布/摘抄，不需要再开编辑器改错别字。

因此 pipeline 固定为三阶段：

```text
DashScope ASR（听写） -> DeepSeek polish（文字编辑） -> title（标题）
```

润色与标题在同一次 LLM 调用中通过 JSON `{"title","body"}` 返回；多 chunk 时仅首 chunk 取标题，无标题则补一次轻量 title 调用。

## 成本与输入选择

| 输入 | 上传体积 | 建议 |
|------|----------|------|
| `.m4a` / `.mp3` | 小 | **首选**，ASR 按音频时长计费，与是否视频无关 |
| `.mp4` | 大 | 会先抽音轨压成 16kHz mono WAV，再上传 |

`proxy-tun.m4a`（~3.7 分钟）属于单次 `qwen3-asr-flash` 调用范围（<5 分钟），比分片更稳。

## DashScope ASR 行为备忘

- 模型：`qwen3-asr-flash`
- 本地文件：`file://{absolute_path}`，SDK 自动上传 OSS
- 空音频/静音：可能返回 `choices[0].message.content = []`，应视为空字符串而非失败
- 长音频（>280s）：ffmpeg 分片后多次调用，段间用 `\n\n` 合并
- 需记录：`request_id`、`api_calls`、`chunk_count` 便于排障

## Polish 行为备忘

- 模型默认：`deepseek-chat`（`configs/.env` 可改 `polish_model`）
- 原则：**只编辑，不创作**——纠错、标点、分段、去口头禅，不添加新观点
- 长文：按 ~3500 字切块多次调用，再 `\n\n` 拼接
- 缓存键：raw ASR 文本 hash + model + language，改 prompt 需 `--force`

## 输出约定

| 文件 | 含义 |
|------|------|
| `{stem}.transcript.txt` | **最终交付物**（标题 + 润色正文） |
| `{stem}.title.txt` | 单独标题 |
| `{stem}.raw.transcript.txt` | ASR 原文对照 |
| `{stem}.transcript.json` | ASR + polish 全量元数据 |
| `{stem}.summary.json` | 运行摘要、缓存状态、API 统计 |

## 媒体处理策略

```text
输入（音频或视频）
  -> ffprobe 探测（不靠后缀硬猜）
  -> 无损抽轨：ffmpeg -c:a copy（视频 -vn；失败则 FLAC）
  -> ASR 标准化：mono 16kHz WAV（仅这一步为听写降采样）
```

`--prepare-only` 只跑前两步，不调用云端 API，适合验格式。

## 实测：proxy-tun.m4a（2026-06-10）

- 时长 223s，m4a 3.0MB；单次 ASR 调用，无需分片
- ASR 中文技术口播质量高，术语（TUN、HTTP/HTTPS、路由）识别正确
- Polish 主要价值：分段（空行）、标点（`四层，`→`四层：`），未添加新内容
- 端到端 ~13s；二次运行双缓存命中，0 API 调用

## 实测：proxy-tun.mp4 / rag-or-not.mp4（2026-06-10）

- `proxy-tun.mp4`：h264+aac，抽轨 copy 成功，ASR 1053 字与 m4a 一致，全链路 ~13s
- `rag-or-not.mp4`：662s 长视频，抽轨 copy 成功；ASR 将自动分片（>280s）

## 已知限制

- SRT 时间轴基于 ASR 分段，字幕文字仍是 raw 段文本（未逐段润色）
- `fun-asr` / `paraformer-v2` 异步 URL 接口未接入（需公网 URL）
- 本地 Whisper 暂缓（用户要求先完善 DashScope 路径）
