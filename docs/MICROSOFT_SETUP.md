# Microsoft sign-in and "Connect Outlook"

Users log in with their Microsoft account (personal `outlook.com` / `hotmail.com`, or a work/school Microsoft 365 account) and then connect that Outlook mailbox to the desk. The desk fetches shipping-instruction / draft-BL mail from it and, after a human approves a draft, replies **from that address** through Microsoft Graph.

There is no shared `novashipaveris@gmail.com` in this mode: `EMAIL_PROVIDER=none`, no `GMAIL_*` lines.

## 1. App registration (once, in Microsoft Entra admin center)

1. `entra.microsoft.com` → **Applications → App registrations → New registration**.
2. Name `NovaShip Averis`. **Supported account types**: *Accounts in any organizational directory and personal Microsoft accounts* (this is what makes `MICROSOFT_TENANT=common` work for outlook.com users). Pick *single tenant* only if every user is inside your own organisation, and then set `MICROSOFT_TENANT=<Directory (tenant) ID>`.
3. **Redirect URI**: platform **Web**, value `http://localhost:8000/auth/microsoft/callback`. Later add the deployed one, e.g. `https://<your-domain>/api/auth/microsoft/callback`.
4. Register → copy the **Application (client) ID**.
5. **Certificates & secrets → New client secret** → copy the secret **Value** immediately (the *Secret ID* column is not it). Rotate before it expires.
6. **API permissions → Add a permission → Microsoft Graph → Delegated**: `User.Read`, `Mail.Read` (or `Mail.ReadWrite`, a superset — either is accepted), `Mail.Send`. `offline_access`, `openid`, `email`, `profile` are requested at sign-in and need no entry. *Grant admin consent* is optional: without it each user consents for themselves.

## 2. Environment

```
EMAIL_PROVIDER=none
EMAIL_SEND_MODE=simulate          # switch to live once one approved reply has been checked
MICROSOFT_CLIENT_ID=<Application (client) ID>
MICROSOFT_CLIENT_SECRET=<secret value>
MICROSOFT_TENANT=common
MICROSOFT_REDIRECT_URI=http://localhost:8000/auth/microsoft/callback
FRONTEND_URL=http://localhost:3000
MAILBOX_TOKEN_KEY=<Fernet key>    # optional; derived from SESSION_SECRET when empty
GMAIL_POLL_INTERVAL_SECONDS=120   # optional: background fetch of every connected mailbox
```

Apply `supabase/migrations/0007_user_mailboxes.sql` when `REPO_BACKEND=supabase`, then `docker compose up -d --force-recreate api`. `GET /auth/config` must show `"microsoft_enabled": true`.

## 3. What the user does

1. `/login` → **Continue with Microsoft** → picks the account. Microsoft asks only for "Sign you in and read your profile"; the desk account is created (role from the register form) or opened.
2. `/welcome` → **Connect Outlook** → Microsoft asks for *Read your mail* and *Send mail as you* once. The card shows the address with `read + send`.
3. Inbox → **Fetch my inbox** pulls the newest messages; cases carry the mailbox tag and the *My mailbox* preset filters them.
4. Approve a draft → with `EMAIL_SEND_MODE=live` the reply is sent from that Outlook address (`NOTIFICATION_SENT.after.from` in the audit trail); with `simulate` nothing leaves and the audit says so.

A password user can do steps 2–4 too. Disconnect from the card at any time; to withdraw the grant on Microsoft's side, remove the app at `myaccount.microsoft.com/consent`.

## 4. Verification, tenants and limits

- Graph delegated mail permissions are **not** in a restricted category: no security assessment is required. Users may see *"unverified"* next to the publisher name; **Publisher Verification** (Entra → App registrations → Branding → verified publisher; needs a Microsoft Partner Network ID and a verified domain) removes it. Personal-account users of unverified multi-tenant apps still get through the consent screen.
- Work/school tenants whose admins block user consent will show *"Need admin approval"*; the admin grants consent once for the organisation (step 1.6) and users proceed.
- Refresh tokens are rotated by Microsoft on every use; the desk stores the new one immediately. A token that stops working (password change, admin revoke, 90 days of inactivity) marks the mailbox `error` on the next poll with a *Reconnect* button.
- Rate limits: Graph allows far more than the desk's poll cadence; keep `GMAIL_POLL_INTERVAL_SECONDS` ≥ 60.

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `error=exchange_failed` on `/login` | redirect URI mismatch (must be byte-identical in Entra and `MICROSOFT_REDIRECT_URI`), or the secret *ID* was pasted instead of its *value* |
| `AADSTS50194` / "not configured as a multi-tenant application" | registration is single-tenant but `MICROSOFT_TENANT=common`: set the tenant id or change *Supported account types* |
| `error=mail_permission_missing` | the user unticked the mail permissions; Connect again and accept them |
| `Need admin approval` | company tenant blocks user consent; ask the admin to grant consent for the app |
| card shows `read only` | `Mail.Send` was not granted or not configured on the registration; Reconnect |
| mailbox `error: Microsoft Graph GET ... 401` | grant revoked or expired; Reconnect |
