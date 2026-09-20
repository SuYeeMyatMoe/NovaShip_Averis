# Vercel deployment — teammate handoff

This repository is prepared for a **single Vercel project** using Vercel Services.

The intended handoff is:

> Import repository -> add environment values -> deploy -> run smoke tests.

No Cloudflare or second backend host is required.

## 1. Import the repository

In Vercel:

1. **Add New -> Project**.
2. Import the repository.
3. Keep the **repository root** as the project root. Do not select only `frontend/`.
4. Root `vercel.json` declares:
   - `frontend/` as Next.js;
   - `backend/` as FastAPI;
   - `/api/*` as the backend route.
5. Add the environment variables from `.env.vercel.example`.
6. Deploy.

The production frontend defaults to same-origin `/api`, so `NEXT_PUBLIC_API_BASE` is normally unnecessary.

## 2. Environment values

Real secret values belong only in Vercel Project -> Settings -> Environment Variables.

Required for the existing live backend:

| Variable | Deployment value |
|---|---|
| `REPO_BACKEND` | `supabase` |
| `TENANT_ID` | `tenant_april` |
| `SUPABASE_URL` | existing project URL |
| `SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_ROLE_KEY` | server-only Supabase credential |
| `SUPABASE_STORAGE_BUCKET` | `documents` |
| `AUTO_SEED` | `0` |
| `LANGGRAPH_CHECKPOINT` | `postgres` |
| `LANGGRAPH_PG_URL` | Session Pooler URI, port 5432, `sslmode=require` |
| `LANGGRAPH_STRICT_MSGPACK` | `true` |
| `VECTOR_STORE` | `supabase` |
| `EMBEDDING_PROVIDER` | `local` |
| `EMBEDDING_DIMENSIONS` | `768` |
| `SUPABASE_VECTOR_DIMENSIONS` | `768` |
| `EMAIL_PROVIDER` | `gmail` |
| `EMAIL_SEND_MODE` | `simulate` |
| `GMAIL_CLIENT_ID` | existing OAuth client |
| `GMAIL_CLIENT_SECRET` | existing OAuth secret |
| `GMAIL_REFRESH_TOKEN` | existing refresh token |
| `GMAIL_ADDRESS` | monitored mailbox |
| `AUTH_MODE` | `demo` for the validated hackathon profile |
| `SESSION_SECRET` | new long random server secret |
| `DEMO_PASSWORD` | must match the existing demo credential set |
| `API_PREFIX` | `/api` |
| `APP_ENV` | `demo` |
| `OCR_ENABLED` | `0` |

Never expose Supabase, Gmail or Postgres secrets through a `NEXT_PUBLIC_*` variable.

Optional:

- `LLM_PROVIDER=none` keeps the currently validated no-external-LLM path.
- If using OpenAI generation, set `LLM_PROVIDER=openai` + `OPENAI_API_KEY` + `LLM_MODEL`.
- `CORS_ORIGINS` is only required by current code when `APP_ENV=production`; set it to the exact Vercel production origin in that profile.

## 3. Existing Supabase project

For the existing shared project:

- do not re-run the seed;
- do not truncate tables;
- keep `AUTO_SEED=0`;
- keep `vector(768)` compatibility;
- keep Postgres checkpoints enabled.

A fresh Supabase project is a separate provisioning workflow and requires the checked-in migrations and seed. It is not required for this handoff.

## 4. First deployed checks

After Vercel succeeds:

```text
GET /api/health
GET /api/rag/info
GET /api/docs
```

Expected:

- health status `ok`;
- backend `SupabaseRepository`;
- vector store `supabase`;
- dimensions `768`;
- chunks > 0.

Then verify in the UI:

- login;
- case list/detail;
- comparison/evidence;
- Ask AI / RAG;
- role-appropriate audit access.

Run one controlled Gmail poll. Re-polling the same provider message must not create another case.

## 5. LangGraph

Use:

```text
LANGGRAPH_CHECKPOINT=postgres
LANGGRAPH_PG_URL=<Supabase Session Pooler :5432 URI with sslmode=require>
```

Vercel instances are replaceable, so process memory is not durable workflow state.

Local validation already proved pause -> restart -> state survives -> resume. Repeat one equivalent deployed smoke test.

## 6. Gmail

Safe handoff profile:

```text
EMAIL_PROVIDER=gmail
EMAIL_SEND_MODE=simulate
```

Inbound Gmail is real. Outbound remains non-delivery until the team intentionally enables it after validating human approval.

When real outbound is enabled, `DELIVERY_UNKNOWN` is intentionally not retried. Reconcile the recipient, subject and approval timestamp against the Gmail Sent mailbox before any manual resend; a lost response may mean Gmail already accepted the message.

Do not run the OAuth authorization helper in Vercel. Obtain/refresh OAuth credentials in a trusted local environment, then store them as Vercel secrets.

## 7. Troubleshooting

### API returns 404

Verify the project was imported from repository root, root `vercel.json` exists, backend entrypoint is `main:app`, and `API_PREFIX=/api` is present.

### Database connection fails

Check that `SUPABASE_URL`, the server key and `LANGGRAPH_PG_URL` are set. Do not print their values. The checkpoint Postgres URI should use Session Pooler port 5432 with SSL.

### RAG dimension error

Use:

```text
EMBEDDING_DIMENSIONS=768
SUPABASE_VECTOR_DIMENSIONS=768
```

### Gmail 401/403

Reauthorize locally and replace the refresh-token secret.

### Outbound item is `DELIVERY_UNKNOWN`

Do not retry automatically. Check the Gmail Sent mailbox using the audited recipient, subject and approval time. Escalate for an explicit operator decision if the outcome cannot be proven.

### Login page exposes demo users/password

That is expected with `AUTH_MODE=demo`. It is not production authentication.

## 8. Do not

- commit `.env`;
- expose server secrets to the browser;
- reseed the live shared database;
- switch deployed checkpoints to memory;
- enable real Gmail outbound just to prove the deployment;
- describe demo authentication as production-secure.
