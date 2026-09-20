"""Hermetic test configuration: never inherit live services from the repository .env."""
from __future__ import annotations

import os

os.environ["REPO_BACKEND"] = "memory"
os.environ["AUTO_SEED"] = "0"
os.environ["AUTH_MODE"] = "demo"
os.environ["EMAIL_SEND_MODE"] = "simulate"
os.environ["VECTOR_STORE"] = "local"
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["LANGGRAPH_CHECKPOINT"] = "memory"
os.environ["LLM_PROVIDER"] = "none"
os.environ["APP_ENV"] = "test"
