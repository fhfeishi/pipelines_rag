# Agent Guide

Canonical guide for agents working on `pipelines-transcript`.

## Mission

Build a focused CLI tool to extract transcript text from video and audio:

```text
MP4 / audio -> ffmpeg normalize -> DashScope ASR -> DeepSeek polish -> transcript.txt
```

## Read Order

1. `strata.md` — launch charter
2. `README.md` — user manual
3. `video_transcript/plan.md` — engineering blueprint
4. `video_transcript/logs.md` — run history
5. `video_transcript/notes.md` — design notes and API behavior memos
6. Code entry points: `video_transcript/cli.py`, `video_transcript/pipeline.py`

## Development Rules

- Keep canonical CLI entries: `uv run transcript` and `uv run -m video_transcript.cli`
- Prefer audio input in docs and examples (smaller uploads, lower cost)
- Always normalize media to mono 16 kHz WAV before cloud ASR
- Preserve transcript cache; `--force` must bypass cache explicitly
- Output files must use `{input_stem}.transcript.*` naming for traceability
- `{stem}.transcript.txt` is the deliverable (`title` + blank line + polished body)
- `{stem}.title.txt` holds the title alone; `{stem}.raw.transcript.txt` keeps ASR verbatim
- Polish + title on by default; `--no-polish` / `--no-title` for debugging only
- Default language is `zh`; use `-l auto` when language is mixed/unknown
- Do not reintroduce PDF/RAG pipeline code into this repo

## Validation Rules

Doc-only changes:

```bash
uv run -m video_transcript.cli --help
```

Behavior changes (requires API keys and a sample file):

```bash
uv run transcript /mnt/d/download/resources/proxy-tun.m4a
```

If API keys or sample media are unavailable, still run `--help` and record what could not be verified.

## Documentation Contract

- `README.md` — concise user manual
- `AGENTS.md` — this file
- `strata.md` — launch charter
- `CLAUDE.md` — shim pointing to `AGENTS.md`
- `video_transcript/plan.md` — technical plan
- `video_transcript/logs.md` — append-only run log

When behavior changes, update `plan.md` and append `logs.md`.

## Current Priority

- Validate polish quality on real samples (e.g. `proxy-tun.m4a`)
- Per-segment polish for SRT if needed
- Long-audio async ASR (`fun-asr`) as optional path
- Local Whisper provider (deferred)
