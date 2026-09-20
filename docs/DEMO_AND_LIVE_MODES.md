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

### LLM_PRIVACY=mask (default)

Company identifiers never reach OpenAI or Gemini in clear text: names, addresses, references and the case's seven-field values are tokenised before the prompt and restored afterwards; every call is audited as `AI_PROVIDER_CALL` with metadata only. Scanned pages are the exception (vision OCR); `ai_privacy.allow_vision_ocr=false` in the policy turns that off. `LLM_PROVIDER=none` sends nothing anywhere.

### OCR_ENABLED=auto

Gemini vision OCR is on whenever `GOOGLE_API_KEY` exists, so scanned PDFs and image attachments become readable instead of escalating as `unreadable`. The scoring replay (`scripts/run_bundle.py`) keeps it off unless `--ocr` is given.

### EMAIL_SEND_MODE=simulate

Real inbound Gmail can be processed, but outbound email is not transmitted.

### Per-user Gmail (Sign in with Google)

Independent of `EMAIL_PROVIDER`. A user who signs in with Google gets their own Gmail connected (`user_mailboxes`, token encrypted). Fetch Inbox then polls *their* mailbox; the shared `GMAIL_ADDRESS` remains available through `source=shared` and the scheduler. In `EMAIL_SEND_MODE=simulate` nothing leaves either mailbox. In `gmail` mode an approved reply on a case that arrived in a user's mailbox is sent from that user's address, which is what the customer expects to see in the thread.

Requires: a Web-application OAuth client (`GOOGLE_OAUTH_CLIENT_ID/SECRET`), its redirect URI (`GOOGLE_OAUTH_REDIRECT_URI`) registered in Google Cloud, migration `0007_user_mailboxes.sql` on Supabase, and each tester added as a test user while the consent screen is unverified. `GMAIL_POLL_INTERVAL_SECONDS` enables background polling; keep it `0` on serverless (Vercel) because there is no long-lived process, and poll from the UI or an external cron instead.

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
- `GOOGLE_OAUTH_CLIENT_ID/SECRET` + `GOOGLE_OAUTH_REDIRECT_URI` for Sign in with Google (optional; the password path keeps working without it)

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
GEMINI_CHAT_MODEL=gemini-3.6-flash
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
- secrets are server-only (including `MAILBOX_TOKEN_KEY`; set it explicitly rather than relying on the `SESSION_SECRET`-derived key);
- CORS is restricted to intended origins.

## Summary

**Current:** deployable, persistent, real Gmail inbound, demo-authenticated, outbound-safe.

**Hackathon live:** import to Vercel, enter env values, keep demo auth + simulated outbound, and run smoke tests.

**Production live:** replace demo authentication, rotate credentials, apply production origin/security settings, and deliberately enable any real outbound/model providers.


## Individual mailboxes only (no shared desk mailbox)

The deployment mode where every person signs in with Microsoft or Google and works their own mailbox:

```
EMAIL_PROVIDER=none
EMAIL_SEND_MODE=live              # or simulate while rehearsing
MICROSOFT_CLIENT_ID=… MICROSOFT_CLIENT_SECRET=… MICROSOFT_TENANT=common
GOOGLE_OAUTH_CLIENT_ID=… GOOGLE_OAUTH_CLIENT_SECRET=…      # optional second provider
GMAIL_POLL_INTERVAL_SECONDS=120
```

No `GMAIL_*` lines. `GET /auth/config` reports `shared_mailbox_configured: false`; the Inbox shows *Connect a mailbox* until one is connected; approving a draft on a case that did not arrive through a connected mailbox returns a retryable `502` (`no mailbox can send this reply`) and the draft goes to `SEND_FAILED`. See `docs/MICROSOFT_SETUP.md`.
