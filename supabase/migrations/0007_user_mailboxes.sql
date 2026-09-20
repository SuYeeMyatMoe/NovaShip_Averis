-- 0007: per-user Gmail connected through "Sign in with Google".
-- One row per desk user. The refresh token is Fernet-encrypted by the API (MAILBOX_TOKEN_KEY /
-- SESSION_SECRET) before it reaches this table; the database never sees plaintext.
create table if not exists user_mailboxes (
  user_id           text primary key references users(id) on delete cascade,
  tenant_id         text not null references tenants(id),
  provider          text not null default 'gmail',
  address           text not null,                    -- the Gmail address that was granted
  google_sub        text,                             -- Google account subject (stable id)
  refresh_token_enc text not null,                    -- encrypted; never plaintext
  scopes            jsonb not null default '[]',
  status            text not null default 'active',   -- active | error | revoked
  connected_at      timestamptz not null default now(),
  last_polled_at    timestamptz,
  last_error        text,
  updated_at        timestamptz not null default now()
);
create index if not exists ix_user_mailboxes_tenant on user_mailboxes(tenant_id, status);

-- Where an inbound message was pulled from (null = the shared desk mailbox / bundle).
alter table email_messages add column if not exists mailbox_user_id text references users(id) on delete set null;
create index if not exists ix_email_mailbox on email_messages(tenant_id, mailbox_user_id);

-- Service-role only: the API layer reads these, browsers never do.
alter table user_mailboxes enable row level security;
