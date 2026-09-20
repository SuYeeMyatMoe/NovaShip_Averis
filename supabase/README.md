# Supabase — schema, RLS and seed

```
supabase/
├── migrations/0001_schema.sql   24 tables, indexes, append-only audit trigger, private `documents` bucket
├── migrations/0002_rls.sql      RLS on every table: tenant-scoped reads, role-gated writes, storage policies
├── migrations/0003_vector.sql   pgvector `case_embeddings` for RAG
├── migrations/0004_accounts.sql `user_credentials` + `revoked_sessions` for the built-in login / register / logout
├── migrations/0007_user_mailboxes.sql `user_mailboxes` (Fernet-encrypted Gmail refresh tokens from Sign in with Google) + `email_messages.mailbox_user_id`
└── seed/
    ├── seed.sql                 one idempotent transaction (ON CONFLICT DO UPDATE) — all tables, 520 cases
    ├── tables/<table>.json      row arrays per table (Table Editor → Import, or scripts)
    └── snapshot.json            rich snapshot loaded by the API in memory mode (REPO_BACKEND=memory)
```

## Tables
`tenants · teams · users · roles · user_roles · party_contacts · email_messages · attachments · cases · case_documents · extracted_fields · comparisons · comparison_fields · case_summaries · action_recommendations · drafts · assignments · shares · notifications · policies · policy_versions · audit_events · processing_errors · ai_runs`

`comparison_fields.field_name` is CHECK-constrained to exactly the seven baseline names; `audit_events` rejects UPDATE/DELETE via trigger.

## Apply
1. SQL editor → run `0001_schema.sql`, `0002_rls.sql`, `0003_vector.sql`, then `0004_accounts.sql`.
2. Seed: run `seed/seed.sql` (≈8 MB; if the editor times out use `psql` or `python -m app.seed.make_seed --push` with `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`).
3. Regenerate after changing the pipeline: `cd backend && python -m app.seed.make_seed [--limit N]`.

## Verify
```sql
select count(*) from cases;                                        -- 520
select count(*) from comparison_fields where result = 'MISMATCH';  -- 72
select count(distinct field_name) from comparison_fields;          -- 7
select status, count(*) from cases group by 1 order by 2 desc;
delete from audit_events where case_id = 'case_email_004';         -- ERROR: audit_events is append-only
```

## Auth model
Backend uses the **service role** key and enforces RBAC in `backend/app/auth/rbac.py`. RLS protects direct client access: JWT claim `app_metadata.tenant_id` (or the `users` row) scopes reads; roles come from `user_roles` or `app_metadata.roles`. Map Supabase Auth users to `users.id` (uuid as text) and insert `user_roles` rows for real sign-in.
