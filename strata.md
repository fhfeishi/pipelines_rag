# Launch Charter: Video/Audio Transcript Tool

## Why This Exists

需要从课程视频、访谈、播客中快速拿到可编辑的文案。直接传视频成本高；抽音轨或直接传音频更省钱。

## Pipeline

```text
MP4 or audio
-> ffmpeg probe + normalize (mono 16kHz WAV)
-> DashScope qwen3-asr-flash (chunk if >5min)
-> DeepSeek polish + title (fix typos, punctuation, paragraphs)
-> transcript.txt (title + body deliverable) / title.txt / raw / json / srt
```

Main entry points:

- `uv run transcript <file.m4a>`
- `uv run -m video_transcript.cli` (alias)

## Success Criteria

- Single MP4 or audio file can be transcribed end to end.
- Output includes polished plain text (ready to use) plus raw ASR and JSON metadata.
- Cache prevents repeat billing for the same source file.
- README documents audio-first workflow for cost savings.

## Current Boundaries

- Cloud ASR only (`dashscope`); local Whisper not yet implemented.
- SRT timestamps are chunk-level for long audio, not word-level.
- No URL download (B站/YouTube) yet.

## Document Contract

- `README.md` — user manual
- `AGENTS.md` — agent development guide
- `video_transcript/plan.md` — technical blueprint
- `video_transcript/logs.md` — append-only decisions and validation
