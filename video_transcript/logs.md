# Logs

## 2026-06-10 — Pivot to video/audio transcript tool

- Removed PDF image RAG pipeline (`rag_langchain/`, `rag_pdfs/`, related docs).
- Added `video_transcript/` package with CLI, ffmpeg media prep, DashScope ASR, cache, outputs.
- Default provider: `dashscope` / `qwen3-asr-flash`.
- Validation: `uv run -m video_transcript.cli --help`.

## 2026-06-10 — Output naming + DashScope hardening

- Output files now use `{input_stem}.transcript.txt/json/srt` and `{input_stem}.summary.json`.
- Cache files: `.cache/{stem}.{key}.cache.json`.
- DashScope: richer error messages, `request_id` tracking, chunk progress logs, API call stats in JSON/summary.
- Long-audio chunk split re-encodes to mono 16kHz WAV for stability.
- Added `--flat` for batch outputs into one directory.

## 2026-06-10 — Full pipeline: ASR + polish on proxy-tun.m4a

**Input:** `/mnt/d/download/resources/proxy-tun.m4a` (223s, 3.0MB audio)

**Command:**
```bash
uv run -m video_transcript.cli /mnt/d/download/resources/proxy-tun.m4a --language zh --format txt,json,srt --force
```

**Results:**
- ASR: 1 API call (`qwen3-asr-flash`), 1053 chars, request_id logged
- Polish: 1 API call (`deepseek-chat`), 1065 chars deliverable
- Elapsed: ~13s end-to-end
- ASR quality: high for zh tech narration; polish mainly added paragraph breaks and punctuation (`四层，` → `四层：`)

**Outputs:** `tmp/outs/proxy-tun/proxy-tun.transcript.txt` (deliverable), `.raw.transcript.txt`, `.json`, `.srt`, `.summary.json`

**Cache:** second run hits both ASR and polish caches (no API calls).

**Stability notes:**
- Single-chunk ASR stable for <5min audio
- Empty-content edge case handled (silence_test)
- Polish prompt constrained to edit-only (no new content added in spot-check)

## 2026-06-10 — Title generation + CLI 精品化（v0.3.0）

**Changes:**
- Polish returns JSON `{title, body}`; deliverable = title + blank line + body
- New `{stem}.title.txt`; SRT first cue uses title when present
- CLI command: `uv run transcript <file>` (`-o`, `-l`, `-f` short flags)
- Python API: `video_transcript.pipeline.run(...)`
- Refactor: `llm.py` shared HTTP client; `pipeline.py` orchestration
- Default language `zh`; cache version bump to `polish_v2_*`

**Re-test proxy-tun.m4a:**
```bash
uv run transcript /mnt/d/download/resources/proxy-tun.m4a --force
```

- Title: `系统代理与TUN模式：为什么开了代理有些软件仍无法使用？`
- Elapsed ~12.7s; cache re-run 0.0s
- `transcript.txt` ready to publish without manual editing

## 2026-06-10 — 音视频格式支持与无损抽轨

**Media pipeline (two-step):**
1. `extract:copy` — 视频抽音轨 / 音频 copy 流，不重编码（失败则 fallback FLAC）
2. `asr:transcode` — 仅 ASR 前转 mono 16kHz WAV（DashScope 上传所需）

**`--prepare-only` 实测（无 API 调用）：**

| 文件 | 类型 | 时长 | 抽轨 | ASR 准备 |
|------|------|------|------|----------|
| proxy-tun.m4a | audio | 223s | copy → .m4a | transcode → 16k wav |
| proxy-tun.mp4 | video | 223s | copy → .m4a (h264+aac) | transcode → 16k wav |
| rag-or-not.mp4 | video | 662s | copy → .m4a | transcode → 16k wav |

**全链路 MP4 实测：**
```bash
uv run transcript /mnt/d/download/resources/proxy-tun.mp4 -o tmp/outs/proxy-tun-from-mp4 --force
```
- ASR 1053 chars（与 m4a 一致），标题+润色正常，~13.3s

## 2026-06-10 — Clip smoke test + push

```bash
uv run transcript proxy-tun.m4a --max-seconds 15 -o tmp/outs/clip-test --force
```

- 15s 片段：ASR 82 chars，pipeline OK，~8.5s
- 新增 `--max-seconds` 节省测试 token；缓存键含 clip 标记
