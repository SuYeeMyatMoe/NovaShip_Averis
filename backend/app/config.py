"""Runtime configuration + repository factory."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from app.repositories.base import BaseRepository
from app.repositories.memory import MemoryRepository

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def resolve_path(value: str, base: Path) -> Path:
    """Resolve relative env paths against the repo (or backend) root, not the process cwd."""
    path = Path(value)
    if path.is_absolute():
        return path
    rooted = (base / path).resolve()
    cwd_path = (Path.cwd() / path).resolve()
    if rooted.exists() or not cwd_path.exists():
        return rooted
    return cwd_path


BUNDLE_DIR = resolve_path(env("BUNDLE_DIR", str(ROOT_DIR / "sdoc-hackathon-bundle")), ROOT_DIR)
SEED_SNAPSHOT = resolve_path(
    env("SEED_SNAPSHOT", str(ROOT_DIR / "supabase" / "seed" / "snapshot.json")),
    ROOT_DIR,
)
REPO_BACKEND = env("REPO_BACKEND", "memory").lower()
AUTO_SEED = env("AUTO_SEED", "1") == "1"


class ConfigurationError(RuntimeError):
    """Raised when an explicitly selected runtime mode is unsafe or incomplete."""


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def auth_mode() -> str:
    mode = os.environ.get("AUTH_MODE", "demo").strip().lower()
    if mode not in {"demo", "local", "jwt"}:
        raise ConfigurationError("AUTH_MODE must be one of: demo, local, jwt")
    return mode


def supabase_server_credentials() -> tuple[str, str]:
    """Return the URL and privileged server credential; never accept public keys."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url:
        raise ConfigurationError("SUPABASE_URL is required for Supabase server access")
    if not key:
        raise ConfigurationError(
            "SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY is required for privileged Supabase server access"
        )
    return url, key


def cors_allowed_origins() -> list[str]:
    configured = os.environ.get("CORS_ORIGINS")
    environment = os.environ.get("APP_ENV", "development").strip().lower()
    if environment not in {"development", "test", "demo"} and not configured:
        raise ConfigurationError("CORS_ORIGINS must be explicitly configured outside development/demo")
    values = configured or "http://localhost:3000,http://127.0.0.1:3000"
    origins = [origin.strip() for origin in values.split(",") if origin.strip()]
    if not origins or "*" in origins:
        raise ConfigurationError("CORS_ORIGINS cannot be empty or wildcard while credentialed CORS is enabled")
    return origins


@lru_cache(maxsize=1)
def get_repo() -> BaseRepository:
    if REPO_BACKEND == "supabase":
        from app.repositories.supabase_repo import SupabaseRepository

        return SupabaseRepository()
    repo = MemoryRepository()
    if AUTO_SEED and SEED_SNAPSHOT.exists():
        repo.load_file(SEED_SNAPSHOT)
    return repo
