# NovaShip Averis API — FastAPI + workers
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./requirements.txt
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/models ./models
COPY backend/pytest.ini ./pytest.ini
COPY backend/data ./data
# demo fixtures + seed snapshot (mounted read-only in compose; copied here so the image is self-contained)
COPY sdoc-hackathon-bundle /data/bundle
COPY supabase/seed/snapshot.json /data/seed/snapshot.json
ENV BUNDLE_DIR=/data/bundle SEED_SNAPSHOT=/data/seed/snapshot.json REPO_BACKEND=memory AUTO_SEED=1 PORT=8000 RAG_DATA_DIR=/app/data
# build the local RAG index so Ask AI works offline out of the box
RUN python -m app.agents.create_index --knowledge-only || true
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
