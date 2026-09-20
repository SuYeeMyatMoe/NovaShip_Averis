# Backend handoff

## Status

The backend is wrapped for team deployment. Final Vercel build and end-to-end testing are team-owned.

Validated before this handoff:

- `SupabaseRepository` healthy;
- real Gmail inbound polling and persistence;
- duplicate Gmail replay protection;
- LangGraph Postgres checkpoint initialization and restart persistence;
- human-review interrupt survival and safe rejection resume;
- Supabase pgvector RAG at 768 dimensions;
- 1,420 RAG chunks indexed: 10 knowledge + 1,410 case chunks;
- knowledge retrieval passed;
- case-scoped retrieval passed with zero cross-case hits;
- cases/emails remained 521 / 521 while indexing;
- 104 backend tests passed under Python 3.11;
- `.env` remained untracked;
- no real outbound email was sent.

Validated pre-deployment baseline:

`387ccc7500625017beaa7148ee7d17a340ab091a`

The Vercel wrapper/configuration was added after that baseline and is intentionally left for team deployment testing.

## Runtime architecture

```text
Vercel project
├── frontend service — Next.js
└── backend service  — FastAPI
      ├── Supabase Database + RLS
      ├── Supabase Storage
      ├── Supabase pgvector
      ├── Postgres LangGraph checkpoints
      └── Gmail API
```

Public routing:

- `/api/*` -> FastAPI
- all other paths -> Next.js

Local Docker/uvicorn keeps the historical unprefixed FastAPI routes.

## Live backing state

Current handoff state:

- cases: 521
- email messages: 521
- RAG embeddings: 1,420
- checkpoint tables: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`

For the existing shared database use `AUTO_SEED=0`. Do not reseed or truncate it.

## Safety profile

- Gmail inbound: real
- Gmail outbound: `simulate`
- authentication: `demo`
- vector store: Supabase
- embedding provider: deterministic local 768-dimensional fallback
- LangGraph checkpoint backend: Postgres

This is a **live-data hackathon demo**, not a production-authenticated system. See [DEMO_AND_LIVE_MODES.md](DEMO_AND_LIVE_MODES.md).

## Team-owned deployment checks

After Vercel import:

1. frontend loads;
2. `GET /api/health` -> `ok` + `SupabaseRepository`;
3. login works;
4. cases/detail/comparison load;
5. `GET /api/rag/info` -> `supabase`, 768 dimensions, non-zero chunks;
6. case-scoped RAG returns no other case;
7. controlled Gmail poll persists at most one case for one provider message;
8. a paused graph still exists after instance replacement/redeploy and can resume;
9. `EMAIL_SEND_MODE=simulate` results in no real email;
10. an ambiguous live Gmail response becomes `DELIVERY_UNKNOWN` and does not resend automatically;
11. no server secret appears in browser bundles or logs.

No further backend feature work is required unless the deployed platform reveals a platform-specific defect.
