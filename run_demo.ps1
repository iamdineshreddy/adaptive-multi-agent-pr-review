# Start the local demo stack for the review prototype.
#
#   1. pgvector Postgres in Docker (host port 5434)
#   2. Backend API           http://127.0.0.1:8000
#   3. Frontend dashboard    http://127.0.0.1:5173
#
# The dashboard calls the API with a development bearer token; its SHA-256 is
# passed to the backend and the raw token to Vite. Use this script instead of
# `uvicorn` / `npm run dev` directly so the dashboard is not served 401s.
#
# Usage:  pwsh -File .\run_demo.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) {
    $docker = "C:\Users\Maddula Dinesh Reddy\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
}
if (-not (Test-Path $docker)) {
    throw "docker CLI not found. Start Docker Desktop and retry."
}

$dbUrl = "postgresql+asyncpg://adaptive:adaptive@localhost:5434/adaptive_review"
$token = "sk-dev-demo-2026"
$tokenHash = "b373addc73b0c0c0ee58f4c9b00a95d946b19d97e03542df801148185ab200c4"

# --- 1. Database ------------------------------------------------------------
$exists = & $docker ps -a --filter "name=adaptive-pg" --format "{{.Names}}" 2>$null
if ($exists -ne "adaptive-pg") {
    Write-Host "Creating adaptive-pg container..."
    & $docker run -d --name adaptive-pg `
        -e POSTGRES_USER=adaptive -e POSTGRES_PASSWORD=adaptive `
        -e POSTGRES_DB=adaptive_review -p 5434:5432 pgvector/pgvector:pg16 | Out-Null
} else {
    & $docker start adaptive-pg | Out-Null
}

Write-Host "Waiting for Postgres on 5434..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    & $docker exec adaptive-pg pg_isready -U adaptive -d adaptive_review *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { throw "Postgres did not become ready. Check: docker logs adaptive-pg" }

# --- 2. Backend -------------------------------------------------------------
$env:ADAPTIVE_DATABASE_URL = $dbUrl
$env:ADAPTIVE_REDIS_URL = "redis://localhost:6379/0"
$env:ADAPTIVE_API_TOKEN_HASHES = "[`"$tokenHash`"]"
$env:ADAPTIVE_API_TOKEN_ROLES = "{`"$tokenHash`":`"admin`"}"

Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory "$root\backend" `
    -WindowStyle Hidden

# --- 3. Frontend ------------------------------------------------------------
$env:VITE_API_TOKEN = $token
Start-Process -FilePath "npm.cmd" `
    -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173" `
    -WorkingDirectory "$root\frontend" `
    -WindowStyle Hidden

Write-Host ""
Write-Host "API:      http://127.0.0.1:8000/health"
Write-Host "Docs:     http://127.0.0.1:8000/api/docs"
Write-Host "Dashboard http://127.0.0.1:5173"
