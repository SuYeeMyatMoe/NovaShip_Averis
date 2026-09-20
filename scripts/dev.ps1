<#
.SYNOPSIS
  Switch NovaShip between Docker, live-reload Docker, and local frontend/backend.

.EXAMPLE
  .\scripts\dev.ps1 docker
  .\scripts\dev.ps1 docker-dev
  .\scripts\dev.ps1 local
  .\scripts\dev.ps1 frontend
  .\scripts\dev.ps1 backend
  .\scripts\dev.ps1 stop
  .\scripts\dev.ps1 status
#>
param(
  [Parameter(Position = 0)]
  [ValidateSet("docker", "docker-dev", "local", "frontend", "backend", "stop", "status")]
  [string]$Mode = "status"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$WebPort = if ($env:WEB_PORT) { [int]$env:WEB_PORT } else { 3000 }
$ApiPort = if ($env:API_PORT) { [int]$env:API_PORT } else { 8000 }

function Get-ListeningPids([int]$Port) {
  $ids = @()
  foreach ($line in (netstat -ano)) {
    if ($line -notmatch "LISTENING") { continue }
    if ($line -notmatch ":$Port\s+") { continue }
    $parts = $line.Trim() -split "\s+"
    $id = 0
    if ([int]::TryParse($parts[-1], [ref]$id) -and $id -gt 4) {
      $ids += $id
    }
  }
  return @($ids | Select-Object -Unique)
}

function Get-ProcessNameSafe([int]$Id) {
  try { return (Get-Process -Id $Id -ErrorAction Stop).ProcessName } catch { return "" }
}

function Stop-LocalUvicorn {
  # Reload parent can die while a multiprocessing child keeps 127.0.0.1:8000.
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "uvicorn|multiprocessing\.spawn" } |
    ForEach-Object {
      Write-Host "Stopping leftover uvicorn python (PID $($_.ProcessId))"
      & taskkill /PID $_.ProcessId /T /F | Out-Null
    }
}

function Stop-LocalListeners([int]$Port) {
  Stop-LocalUvicorn
  foreach ($id in (Get-ListeningPids $Port)) {
    $name = Get-ProcessNameSafe $id
    if ($name -match "^(node|python|python3)$" -or ($name -eq "" -and $Port -eq $ApiPort)) {
      Write-Host "Stopping local $name (PID $id) on port $Port"
      & taskkill /PID $id /T /F 2>$null | Out-Null
    }
  }
}

function Show-Status {
  Write-Host "=== ports ==="
  netstat -ano | Select-String ":$WebPort ", ":$ApiPort " | Select-String "LISTENING"
  Write-Host "`n=== docker compose ==="
  docker compose -f docker-compose.yml ps
}

function Stop-Stack {
  Write-Host "Stopping Compose api/web and local listeners on $WebPort / $ApiPort"
  docker compose -f docker-compose.yml -f docker-compose.dev.yml stop api web 2>$null
  docker compose -f docker-compose.yml stop api web 2>$null
  Stop-LocalListeners $WebPort
  Stop-LocalListeners $ApiPort
  Start-Sleep -Seconds 1
}

function Assert-PortFree([int]$Port, [string]$What) {
  $ids = Get-ListeningPids $Port
  $blocking = @()
  foreach ($id in $ids) {
    $name = Get-ProcessNameSafe $id
    if ($name -and $name -notmatch "docker|com.docker|wslrelay|vpnkit") {
      $blocking += "$name PID $id"
    }
  }
  if ($blocking.Count -gt 0) {
    throw "Port $Port still in use ($($blocking -join ', ')). Close it, or run: .\scripts\dev.ps1 stop"
  }
}

switch ($Mode) {
  "status" { Show-Status }

  "stop" {
    Stop-Stack
    Show-Status
  }

  "docker" {
    Write-Host "Mode: Docker demo images (rebuilds after code changes)"
    Stop-LocalListeners $WebPort
    Stop-LocalListeners $ApiPort
    docker compose -f docker-compose.yml up -d --build
    Show-Status
    Write-Host "`nUI  http://localhost:$WebPort"
    Write-Host "API http://localhost:$ApiPort/docs"
  }

  "docker-dev" {
    Write-Host "Mode: Docker live reload (source mounts). Edit files on the host."
    Stop-LocalListeners $WebPort
    Stop-LocalListeners $ApiPort
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
    Show-Status
    Write-Host "`nUI  http://localhost:$WebPort  (Next.js --reload)"
    Write-Host "API http://localhost:$ApiPort/docs  (uvicorn --reload)"
  }

  "local" {
    Write-Host "Mode: local frontend + local backend"
    docker compose -f docker-compose.yml -f docker-compose.dev.yml stop api web 2>$null
    docker compose -f docker-compose.yml stop api web 2>$null
    Stop-LocalListeners $WebPort
    Stop-LocalListeners $ApiPort
    Assert-PortFree $ApiPort "API"
    Assert-PortFree $WebPort "UI"
    $backend = Join-Path $Root "backend"
    $frontend = Join-Path $Root "frontend"
    Start-Process powershell -ArgumentList @(
      "-NoExit", "-Command",
      "Set-Location `"$backend`"; python -m uvicorn app.main:app --reload --port $ApiPort"
    )
    Start-Process powershell -ArgumentList @(
      "-NoExit", "-Command",
      "Set-Location `"$frontend`"; if (-not (Test-Path node_modules)) { npm install }; npm run dev"
    )
    Write-Host "Started two terminals."
    Write-Host "UI  http://localhost:$WebPort"
    Write-Host "API http://localhost:$ApiPort/docs"
  }

  "frontend" {
    Write-Host "Mode: local Next.js + Docker API"
    docker compose -f docker-compose.yml stop web 2>$null
    Stop-LocalListeners $WebPort
    docker compose -f docker-compose.yml up -d api
    Assert-PortFree $WebPort "UI"
    $frontend = Join-Path $Root "frontend"
    Start-Process powershell -ArgumentList @(
      "-NoExit", "-Command",
      "Set-Location `"$frontend`"; if (-not (Test-Path node_modules)) { npm install }; npm run dev"
    )
    Write-Host "UI  http://localhost:$WebPort  (local, hot reload)"
    Write-Host "API http://localhost:$ApiPort/docs  (Docker)"
  }

  "backend" {
    Write-Host "Mode: local uvicorn + Docker web"
    docker compose -f docker-compose.yml stop api 2>$null
    Stop-LocalListeners $ApiPort
    docker compose -f docker-compose.yml up -d web
    Assert-PortFree $ApiPort "API"
    $backend = Join-Path $Root "backend"
    Start-Process powershell -ArgumentList @(
      "-NoExit", "-Command",
      "Set-Location `"$backend`"; python -m uvicorn app.main:app --reload --port $ApiPort"
    )
    Write-Host "UI  http://localhost:$WebPort  (Docker)"
    Write-Host "API http://localhost:$ApiPort/docs  (local, --reload)"
  }
}
