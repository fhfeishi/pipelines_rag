#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已创建 .env，请先填入 DEEPSEEK_API_KEY。"
fi
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
