"""
NovaShip Averis API - FastAPI entrypoint.

    uvicorn app.main:app --reload --port 8000
    open http://localhost:8000/docs
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

# Direct local runs start in backend/, while the shared environment file lives
# at the repository root. Existing process/container variables keep precedence.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agent_routes import router as agent_router
from app.api.auth_routes import router as auth_router, seed_demo_credentials
from app.api.google_auth_routes import router as google_auth_router
from app.api.routes import router
from app.auth.accounts import validate_session_configuration
from app.config import auth_mode, cors_allowed_origins, get_repo

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format='{"t":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
log = logging.getLogger("novaship")

# Vercel Services exposes FastAPI under /api. Local Docker / uvicorn keeps the
# historical root-level API. API_PREFIX can override either behavior.
_default_prefix = "/api" if os.environ.get("VERCEL") else ""
API_PREFIX = os.environ.get("API_PREFIX", _default_prefix).strip()
if API_PREFIX and not API_PREFIX.startswith("/"):
    API_PREFIX = "/" + API_PREFIX
API_PREFIX = API_PREFIX.rstrip("/")

app = FastAPI(
    title="NovaShip Averis - AI Shipping Inbox & SI<->BL Verification",
    version="1.0.0",
    description="Ingest -> secure -> classify -> extract -> deterministic seven-field compare -> evidence -> draft -> human approval -> notify -> audit.",
    docs_url=f"{API_PREFIX}/docs" if API_PREFIX else "/docs",
    redoc_url=f"{API_PREFIX}/redoc" if API_PREFIX else "/redoc",
    openapi_url=f"{API_PREFIX}/openapi.json" if API_PREFIX else "/openapi.json",
)

app.add_middleware(CORSMiddleware, allow_origins=cors_allowed_origins(), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.time()
    resp = await call_next(request)
    resp.headers["X-Process-Time-Ms"] = str(int((time.time() - t0) * 1000))
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal error", "category": "DATABASE_ERROR", "step": request.url.path, "recovery": "Retry; if it persists check backend logs.", "retryable": True})


@app.on_event("startup")
def startup() -> None:
    auth_mode()
    validate_session_configuration()
    repo = get_repo()
    n_cases = len(repo.list_cases())
    n_emails = len(repo.list_emails())
    repo.list_users()
    seeded = seed_demo_credentials() if auth_mode() == "demo" else 0
    log.info("repository=%s cases=%d emails=%d llm=%s demo_credentials_seeded=%d", type(repo).__name__, n_cases, n_emails, os.environ.get("LLM_PROVIDER", "none"), seeded)
    from app.services.mailbox_poller import start_background_poller

    start_background_poller()  # no-op unless GMAIL_POLL_INTERVAL_SECONDS > 0
    from app.ai.privacy import privacy_mode

    if os.environ.get("LLM_PROVIDER", "none").lower() != "none" and privacy_mode() == "off":
        log.warning("LLM_PRIVACY=off with an external provider: company identifiers will reach %s unmasked", os.environ.get("LLM_PROVIDER"))


app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(google_auth_router, prefix=API_PREFIX)
app.include_router(router, prefix=API_PREFIX)
app.include_router(agent_router, prefix=API_PREFIX)
