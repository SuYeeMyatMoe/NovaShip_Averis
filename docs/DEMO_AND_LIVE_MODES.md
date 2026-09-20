# Demo mode and going live

"Demo mode" can refer to several independent fallback switches. The important one for authentication is `AUTH_MODE`.

## Current validated handoff profile

```text
REPO_BACKEND=supabase
AUTH_MODE=demo
EMAIL_PROVIDER=gmail
EMAIL_SEND_MODE=simulate
LANGGRAPH_CHECKPOINT=postgres
VECTOR_STORE=supabase
EMBEDDING_PROVIDER=local
LLM_PROVIDER=none
```

The **data path is live and persistent**. The authentication, model provider and outbound-mail choices are intentionally demo-safe.

## What AUTH_MODE=demo means

With `AUTH_MODE=demo`:

1. normal login still issues signed `nsa.*` sessions;
2. seeded users can use the shared `DEMO_PASSWORD`;
3. `GET /auth/config` exposes demo-account convenience data;
4. the backend accepts `X-User-Id` as a testing/demo identity shortcut;
5. self-registration is available for least-privilege Operations Staff;
6. missing demo password hashes may be seeded automatically.

A request with no credential still receives 401. Demo mode does **not** mean authentication is disabled.

It is not suitable for unrestricted production because a shared password and `X-User-Id` are intentional test conveniences.

## Separate demo/fallback settings

### REPO_BACKEND=memory

Fully local/offline persistence. Vercel should use `REPO_BACKEND=supabase`.

### LLM_PROVIDER=none

No external chat model is required. Deterministic comparison remains authoritative and fallback generation is used.

### EMBEDDING_PROVIDER=local

RAG uses deterministic local hashing embeddings, configured at 768 dimensions and persisted in Supabase pgvector.

### EMAIL_SEND_MODE=simulate

Real inbound Gmail can be processed, but outbound email is not transmitted.

## Hackathon live

For a controlled hackathon deployment, keep:

- `AUTH_MODE=demo`
- `APP_ENV=demo`
- strong random `SESSION_SECRET`
- `REPO_BACKEND=supabase`
- `AUTO_SEED=0`
- `EMAIL_PROVIDER=gmail`
- `EMAIL_SEND_MODE=simulate`
- `LANGGRAPH_CHECKPOINT=postgres`
- `VECTOR_STORE=supabase`

This is a **live deployed demo**: Vercel, Supabase, Gmail inbound and workflow persistence are real, while authentication remains demo-oriented and outbound email remains safe.

## Moving to production

Do not call the system production-ready by changing only `APP_ENV`.

### Option A: local application auth

Set:

```text
AUTH_MODE=local
SELF_REGISTRATION_ENABLED=0
```

This disables `X-User-Id`, demo-account disclosure and demo credential seeding while retaining signed application sessions.

Before public use, rotate every account still using the shared demo password. There is no end-user password-management UI in the current handoff, so credential rotation is an operator/admin task.

### Option B: Supabase Auth JWT

Set `AUTH_MODE=jwt` only after real Supabase Auth identities exist and are mapped through `public.users.auth_user_id`.

The current browser login screen is built around local `/auth/login`. A JWT deployment also requires frontend Supabase Auth wiring.

### Production environment

Use:

```text
APP_ENV=production
CORS_ORIGINS=https://<exact-production-domain>
AUTO_SEED=0
```

Keep a unique strong `SESSION_SECRET` when local sessions are used.

### External LLM

Optional:

```text
LLM_PROVIDER=openai
OPENAI_API_KEY=<server secret>
```

or

```text
LLM_PROVIDER=gemini
GOOGLE_API_KEY=<server secret>
GEMINI_CHAT_MODEL=gemini-2.0-flash
```

The seven-field verdict remains deterministic regardless of LLM choice.

### Embeddings

The live column is `vector(768)`. A replacement embedding provider must return 768 dimensions or the vector schema/index must be migrated and reindexed.

### Real outbound Gmail

Keep `EMAIL_SEND_MODE=simulate` until the deployed human-approval and recipient controls are verified. Only then intentionally set `EMAIL_SEND_MODE=gmail`.

Outbound delivery is fail-closed. Drafts and shares are persisted as `DELIVERING` before Gmail is called. A confirmed provider response reaches `SENT`; an interrupted or ambiguous response becomes `DELIVERY_UNKNOWN`, blocks automatic resend, and must be reconciled against the Gmail Sent mailbox before an operator takes further action.

## Production gate

Before claiming production readiness verify:

- no shared demo passwords remain;
- `X-User-Id` is rejected;
- self-registration policy is intentional;
- RBAC/tenant boundaries hold;
- session revocation works;
- Storage remains private/signed;
- RAG shows zero cross-case leakage;
- LangGraph survives instance replacement;
- real outbound email, if enabled, is human-approved and audited;
- secrets are server-only;
- CORS is restricted to intended origins.

## Summary

**Current:** deployable, persistent, real Gmail inbound, demo-authenticated, outbound-safe.

**Hackathon live:** import to Vercel, enter env values, keep demo auth + simulated outbound, and run smoke tests.

**Production live:** replace demo authentication, rotate credentials, apply production origin/security settings, and deliberately enable any real outbound/model providers.
