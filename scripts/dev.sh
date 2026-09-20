#!/usr/bin/env bash
# Switch NovaShip between Docker, live-reload Docker, and local frontend/backend.
# Usage: ./scripts/dev.sh docker|docker-dev|local|frontend|backend|stop|status
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODE="${1:-status}"
WEB_PORT="${WEB_PORT:-3000}"
API_PORT="${API_PORT:-8000}"

listening_pids() {
  local port="$1"
  netstat -ano 2>/dev/null | tr -d '\r' | awk -v p=":$port" '
    /LISTENING/ && index($0, p) {
      pid=$NF
      if (pid + 0 > 4) print pid
    }
  ' | sort -u
}

proc_name() {
  local pid="$1"
  tasklist //FI "PID eq $pid" 2>/dev/null | awk -v id="$pid" '
    NR>3 && $2==id { print $1; exit }
  '
}

stop_local_uvicorn() {
  # Reload parent can die while a multiprocessing child keeps 127.0.0.1:8000.
  powershell.exe -NoProfile -Command "
    Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue |
      Where-Object { \$_.CommandLine -match 'uvicorn|multiprocessing\\.spawn' } |
      ForEach-Object {
        Write-Host \"Stopping leftover uvicorn python (PID \$(\$_.ProcessId))\"
        & taskkill /PID \$_.ProcessId /T /F | Out-Null
      }
  " 2>/dev/null || true
}

stop_local_listeners() {
  local port="$1"
  local pid name
  stop_local_uvicorn
  for pid in $(listening_pids "$port"); do
    name="$(proc_name "$pid")"
    case "$name" in
      node.exe|python.exe|python3.exe|node|python|python3|"")
        if [ -n "$name" ] || [ "$port" = "$API_PORT" ]; then
          echo "Stopping local ${name:-orphaned} (PID $pid) on port $port"
          taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
        fi
        ;;
    esac
  done
}

show_status() {
  echo "=== ports ==="
  netstat -ano 2>/dev/null | tr -d '\r' | awk -v w=":$WEB_PORT" -v a=":$API_PORT" '
    /LISTENING/ && (index($0, w) || index($0, a)) { print }
  '
  echo
  echo "=== docker compose ==="
  docker compose -f docker-compose.yml ps
}

stop_stack() {
  echo "Stopping Compose api/web and local listeners on $WEB_PORT / $API_PORT"
  docker compose -f docker-compose.yml -f docker-compose.dev.yml stop api web >/dev/null 2>&1 || true
  docker compose -f docker-compose.yml stop api web >/dev/null 2>&1 || true
  stop_local_listeners "$WEB_PORT"
  stop_local_listeners "$API_PORT"
  sleep 1
}

assert_port_free() {
  local port="$1"
  local pid name
  for pid in $(listening_pids "$port"); do
    name="$(proc_name "$pid")"
    case "$name" in
      *docker*|com.docker*|wslrelay*|vpnkit*) ;;
      "") ;;
      *)
        echo "Port $port still in use ($name PID $pid). Close it, or run: ./scripts/dev.sh stop" >&2
        exit 1
        ;;
    esac
  done
}

case "$MODE" in
  status) show_status ;;
  stop)
    stop_stack
    show_status
    ;;
  docker)
    echo "Mode: Docker demo images (rebuilds after code changes)"
    stop_local_listeners "$WEB_PORT"
    stop_local_listeners "$API_PORT"
    docker compose -f docker-compose.yml up -d --build
    show_status
    echo
    echo "UI  http://localhost:$WEB_PORT"
    echo "API http://localhost:$API_PORT/docs"
    ;;
  docker-dev)
    echo "Mode: Docker live reload (source mounts). Edit files on the host."
    stop_local_listeners "$WEB_PORT"
    stop_local_listeners "$API_PORT"
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
    show_status
    echo
    echo "UI  http://localhost:$WEB_PORT  (Next.js --reload)"
    echo "API http://localhost:$API_PORT/docs  (uvicorn --reload)"
    ;;
  local)
    echo "Mode: local frontend + local backend"
    docker compose -f docker-compose.yml -f docker-compose.dev.yml stop api web >/dev/null 2>&1 || true
    docker compose -f docker-compose.yml stop api web >/dev/null 2>&1 || true
    stop_local_listeners "$WEB_PORT"
    stop_local_listeners "$API_PORT"
    assert_port_free "$API_PORT"
    assert_port_free "$WEB_PORT"
    echo "Start these in two terminals:"
    echo "  cd backend && python -m uvicorn app.main:app --reload --port $API_PORT"
    echo "  cd frontend && npm install && npm run dev"
    echo
    echo "UI  http://localhost:$WEB_PORT"
    echo "API http://localhost:$API_PORT/docs"
    ;;
  frontend)
    echo "Mode: local Next.js + Docker API"
    docker compose -f docker-compose.yml stop web >/dev/null 2>&1 || true
    stop_local_listeners "$WEB_PORT"
    docker compose -f docker-compose.yml up -d api
    assert_port_free "$WEB_PORT"
    echo "Start the UI:"
    echo "  cd frontend && npm install && npm run dev"
    echo
    echo "UI  http://localhost:$WEB_PORT  (local, hot reload)"
    echo "API http://localhost:$API_PORT/docs  (Docker)"
    ;;
  backend)
    echo "Mode: local uvicorn + Docker web"
    docker compose -f docker-compose.yml stop api >/dev/null 2>&1 || true
    stop_local_listeners "$API_PORT"
    docker compose -f docker-compose.yml up -d web
    assert_port_free "$API_PORT"
    echo "Start the API:"
    echo "  cd backend && python -m uvicorn app.main:app --reload --port $API_PORT"
    echo
    echo "UI  http://localhost:$WEB_PORT  (Docker)"
    echo "API http://localhost:$API_PORT/docs  (local, --reload)"
    ;;
  *)
    echo "Usage: $0 docker|docker-dev|local|frontend|backend|stop|status" >&2
    exit 1
    ;;
esac
