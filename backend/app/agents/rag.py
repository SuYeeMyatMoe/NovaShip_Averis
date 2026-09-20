"""
RAG for Ask-AI: embeddings + vector store over (a) the `backend/data/` knowledge
folder (policy, glossary, port aliases) and (b) case content persisted in Supabase
(email body, SI/BL text, summary, comparison message).

    EMBEDDING_PROVIDER = gemini | openai | local        (default: local = offline hashing, demo only)
    GOOGLE_API_KEY     -> Gemini  text-embedding-004 (768 dims)        https://aistudio.google.com/app/apikey
    OPENAI_API_KEY     -> OpenAI  text-embedding-3-small (1536 dims)   https://platform.openai.com/api-keys
    VECTOR_STORE       = supabase | local                (default: local -> backend/data/index.json)

Supabase store needs migration 0003_vector.sql (pgvector table `case_embeddings` + RPC `match_case_chunks`).
Build/refresh the index with:  python -m app.agents.create_index
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
LOCAL_INDEX = DATA_DIR / "index.json"


@dataclass
class Chunk:
    id: str
    text: str
    source: str            # policy | glossary | case:<case_id> | doc:<attachment_id>
    case_id: Optional[str] = None
    tenant_id: str = "tenant_april"
    metadata: dict[str, Any] | None = None


# ----------------------------------------------------------------------------- embeddings
class Embedder:
    def __init__(self) -> None:
        from app.config import ConfigurationError

        self.provider = os.environ.get("EMBEDDING_PROVIDER", "local").lower()
        self.dims = int(os.environ.get("EMBEDDING_DIMENSIONS", "256"))
        if self.dims <= 0:
            raise ConfigurationError("EMBEDDING_DIMENSIONS must be a positive integer")
        self._impl = None
        try:
            if self.provider == "gemini":
                if not os.environ.get("GOOGLE_API_KEY"):
                    raise ConfigurationError("EMBEDDING_PROVIDER=gemini requires GOOGLE_API_KEY")
                from langchain_google_genai import GoogleGenerativeAIEmbeddings

                self._impl = GoogleGenerativeAIEmbeddings(model=os.environ.get("GEMINI_EMBEDDING_MODEL", "models/text-embedding-004"))
                self.dims = 768
            elif self.provider == "openai":
                if not os.environ.get("OPENAI_API_KEY"):
                    raise ConfigurationError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
                from langchain_openai import OpenAIEmbeddings

                self.dims = int(os.environ.get("EMBEDDING_DIMENSIONS", "768"))
                self._impl = OpenAIEmbeddings(model=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"), dimensions=self.dims)
            elif self.provider != "local":
                raise ConfigurationError("EMBEDDING_PROVIDER must be local, gemini, or openai")
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError(f"Embedding provider initialization failed ({type(exc).__name__})") from exc

    def _validate(self, vectors: list[list[float]]) -> list[list[float]]:
        if any(len(vector) != self.dims for vector in vectors):
            raise ValueError(f"embedding provider returned a vector dimension other than configured {self.dims}")
        return vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._impl is not None:
            return self._validate(self._impl.embed_documents(texts))
        return self._validate([self._local(t) for t in texts])

    def embed_query(self, text: str) -> list[float]:
        if self._impl is not None:
            return self._validate([self._impl.embed_query(text)])[0]
        return self._validate([self._local(text)])[0]

    def _local(self, text: str) -> list[float]:
        """Deterministic bag-of-words hashing embedding (offline fallback, keyword-level recall only)."""
        vec = [0.0] * self.dims
        for tok in re.findall(r"[a-z0-9]{2,}", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % self.dims] += 1.0
            vec[(h >> 8) % self.dims] += 0.5
        n = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / n for v in vec]


# ----------------------------------------------------------------------------- stores
class LocalStore:
    def __init__(self, path: Path = LOCAL_INDEX) -> None:
        self.path = path
        self.rows: list[dict[str, Any]] = []
        if path.exists():
            try:
                self.rows = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.rows = []

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        by_id = {r["id"]: r for r in self.rows}
        for c, v in zip(chunks, vectors):
            by_id[c.id] = {"id": c.id, "text": c.text, "source": c.source, "case_id": c.case_id, "tenant_id": c.tenant_id, "metadata": c.metadata or {}, "embedding": v}
        self.rows = list(by_id.values())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rows), encoding="utf-8")

    def search(self, qv: list[float], k: int = 6, case_id: Optional[str] = None, sources: Optional[list[str]] = None) -> list[dict[str, Any]]:
        out = []
        for r in self.rows:
            if case_id and r.get("case_id") and r["case_id"] != case_id:
                continue  # never leak other cases
            if sources and not any(r["source"].startswith(s) for s in sources):
                continue
            e = r["embedding"]
            if len(e) != len(qv):
                continue
            score = sum(a * b for a, b in zip(e, qv))
            out.append({**{k2: v for k2, v in r.items() if k2 != "embedding"}, "score": round(score, 4)})
        out.sort(key=lambda x: -x["score"])
        return out[:k]

    def count(self) -> int:
        return len(self.rows)


class SupabaseStore:
    """pgvector-backed store (migration 0003_vector.sql)."""

    def __init__(self) -> None:
        from supabase import create_client
        from app.config import supabase_server_credentials

        url, key = supabase_server_credentials()
        self.client = create_client(url, key)
        self.tenant = os.environ.get("TENANT_ID", "tenant_april")

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        rows = [{"id": c.id, "tenant_id": self.tenant, "case_id": c.case_id, "source": c.source, "content": c.text, "metadata": c.metadata or {}, "embedding": v} for c, v in zip(chunks, vectors)]
        for i in range(0, len(rows), 200):
            self.client.table("case_embeddings").upsert(rows[i:i + 200]).execute()

    def search(self, qv: list[float], k: int = 6, case_id: Optional[str] = None, sources: Optional[list[str]] = None) -> list[dict[str, Any]]:
        res = self.client.rpc("match_case_chunks", {"query_embedding": qv, "match_count": k, "p_tenant": self.tenant, "p_case_id": case_id, "p_sources": sources or []}).execute()
        return [{"id": r["id"], "text": r["content"], "source": r["source"], "case_id": r.get("case_id"), "metadata": r.get("metadata") or {}, "score": round(r.get("similarity", 0), 4)} for r in res.data]

    def count(self) -> int:
        res = self.client.table("case_embeddings").select("id", count="exact").eq("tenant_id", self.tenant).execute()
        return res.count or 0


# ----------------------------------------------------------------------------- facade
class RAG:
    def __init__(self) -> None:
        from app.config import ConfigurationError

        self.embedder = Embedder()
        self.store_kind = os.environ.get("VECTOR_STORE", "local").lower()
        if self.store_kind == "supabase":
            expected_dims = int(os.environ.get("SUPABASE_VECTOR_DIMENSIONS", "768"))
            if self.embedder.dims != expected_dims:
                raise ConfigurationError(
                    f"Supabase vector dimension is {expected_dims}, but {self.embedder.provider} embeddings are configured for {self.embedder.dims}"
                )
            self.store: LocalStore | SupabaseStore = SupabaseStore()
        elif self.store_kind == "local":
            self.store = LocalStore()
        else:
            raise ConfigurationError("VECTOR_STORE must be local or supabase")

    def index(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        vectors = self.embedder.embed([c.text for c in chunks])
        self.store.upsert(chunks, vectors)
        return len(chunks)

    def search(self, query: str, *, case_id: Optional[str] = None, k: int = 6, sources: Optional[list[str]] = None) -> list[dict[str, Any]]:
        return self.store.search(self.embedder.embed_query(query), k=k, case_id=case_id, sources=sources)

    def info(self) -> dict[str, Any]:
        return {"embedding_provider": self.embedder.provider, "dims": self.embedder.dims, "vector_store": self.store_kind, "chunks": self.store.count()}


_rag: Optional[RAG] = None


def get_rag() -> RAG:
    global _rag
    if _rag is None:
        _rag = RAG()
    return _rag


# ----------------------------------------------------------------------------- chunking helpers
def chunk_text(text: str, size: int = 700, overlap: int = 80) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out


def knowledge_chunks(data_dir: Path = DATA_DIR) -> list[Chunk]:
    """Markdown files in backend/data/ -> chunks. File name becomes the source prefix (policy, glossary, ports...)."""
    chunks: list[Chunk] = []
    for p in sorted(data_dir.glob("*.md")):
        source = p.stem.split("_")[0]
        for i, piece in enumerate(chunk_text(p.read_text(encoding="utf-8"))):
            chunks.append(Chunk(id=f"{p.stem}:{i}", text=piece, source=source, metadata={"file": p.name, "part": i}))
    return chunks


def case_chunks(case, email) -> list[Chunk]:
    """Case content -> chunks scoped by case_id (so Ask-AI can only retrieve the current case)."""
    cid = case.id
    chunks: list[Chunk] = [Chunk(id=f"case:{cid}:email", text=f"Subject: {email.subject}\nFrom: {email.sender}\n\n{email.body[:3000]}", source=f"case:{cid}", case_id=cid, metadata={"kind": "email"})]
    if case.summary:
        chunks.append(Chunk(id=f"case:{cid}:summary", text=case.summary.text, source=f"case:{cid}", case_id=cid, metadata={"kind": "summary"}))
    if case.comparison:
        lines = [case.comparison.message] + [f"{f.label}: SI='{f.si_original}' BL='{f.bl_original}' -> {f.result.value}" for f in case.comparison.fields]
        chunks.append(Chunk(id=f"case:{cid}:comparison", text="\n".join(lines), source=f"case:{cid}", case_id=cid, metadata={"kind": "comparison"}))
    for a in email.attachments:
        if a.raw_text:
            for i, piece in enumerate(chunk_text(a.raw_text, 900, 100)[:6]):
                chunks.append(Chunk(id=f"doc:{a.id}:{i}", text=f"[{a.file_name} / {a.detected_type.value}]\n{piece}", source=f"doc:{a.id}", case_id=cid, metadata={"kind": "document", "file": a.file_name}))
    return chunks
