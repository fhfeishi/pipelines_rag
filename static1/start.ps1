$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "已创建 .env，请先填入 DEEPSEEK_API_KEY。"
}
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
