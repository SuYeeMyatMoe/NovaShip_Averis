"""Vercel Services entrypoint.

The normal local entrypoint remains app.main:app. Vercel Services expects an
importable main:app at the backend service root, so this module re-exports the
same FastAPI application.
"""
from app.main import app

__all__ = ["app"]
