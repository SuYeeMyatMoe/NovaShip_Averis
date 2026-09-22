# E-mail test kit — end-to-end checklist for a connected Outlook / Gmail

Build the kit once (`python backend/scripts/build_testkit.py` → `testkit/`), send the eight mails **from your connected mailbox to yourself**, then walk this list top to bottom. Every step says where to look and what value to expect. Tick as you go.

| # | Folder | What it proves | Expected after fetch |
|---|---|---|---|
| 01 | `01_mismatch_needs_person` | compare finds 2 of 7 fields wrong → a person decides | `BL_COMPARISON` · `HUMAN_REVIEW` · `ATTENTION_REQUIRED` · mismatches `container_count`, `gross_weight_kg` · 1 correction draft |
| 02 | `02_match_draft_ready` | clean pair → confirmation draft | `BL_COMPARISON` · `DRAFT_READY` · `PASSED` · 1 draft |
| 03 | `03_incomplete_si_only` | missing document → request, later completed by upload | `WAITING_DOCUMENTS` · `review_reason: missing_attachment` · 1 request draft |
| 04 | `04_info_no_action` | information only | `GENERAL` · `OPERATIONAL_UPDATE` · `NO_ACTION_INFO` · no draft |
| 05 | `05_spam_security` | phishing detection | `SPAM` · `NO_ACTION_REQUIRED` · `NO_ACTION_INFO` · security card flags the link · no draft |
| 06 | `06_ocr_image_si` | OCR of a **PNG photo** of the SI | SI `ocr: true`, confidence ≤ 0.9 · `PASSED` · `DRAFT_READY` |
| 07 | `07_ocr_scanned_pdf_pair` | OCR of two **image-only PDFs** | both docs OCR'd · all seven `MATCH` · "No mismatch detected." |
| 08 | `08_invoice_query` | another intent | `INVOICE_QUERY` · `CLASSIFIED` · 1 reply draft |

## 0. Pre-flight (2 min)

- [ ] `.env`: `EMAIL_SEND_MODE=simulate` for the first pass (switch to `live` in step 12), `OCR_ENABLED=auto` and `GOOGLE_API_KEY` set, `LLM_PROVIDER` set (drafts/Ask AI), `EMAIL_PROVIDER=none`.
- [ ] Guide page → *Your mailbox* shows your address with **read + send** (`GET /me/mailbox` → `can_send: true`).
- [ ] Policies page → *Operator guard · learned for you* says **fixed until 20 actions** (note it; step 7 checks that it learns).
- [ ] `docker compose up -d --force-recreate api` after any `.env` change.

## 1. Send the mails (5 min)

- [ ] For each folder 01→08: new mail **to your own address**, subject = `subject.txt`, body = `body.txt`, attach all files in `attachments/` (03 has one file; 04, 05, 08 have none). Keep the `[NS-TEST nn]` prefix — it is how you'll find them.
- [ ] Do **not** attach anything from `03_incomplete_si_only/later/` yet.

## 2. Fetch (1 min)

- [ ] Inbox → **Fetch my inbox** → toast `Fetched 8 new case(s) from <your address>`.
- [ ] Press it again → `No new mail … (8 already ingested)` (idempotent: `duplicates_skipped: 8`).
- [ ] Preset **My mailbox** → exactly the eight `[NS-TEST]` rows, each with your address badge. Note their case ids.

## 3. Per-case checks (15 min) — open each case

Classification card = category, intent, confidence, `intent_source` (rule / model / llm). Seven-fields card = SI value, BL value, status per field. Security card = sender/link signals. Drafts tab.

- [ ] **01**: category `BL_COMPARISON`, status `HUMAN_REVIEW`, comparison `ATTENTION_REQUIRED`; seven-fields: `container_count` and `gross_weight_kg` red `MISMATCH` with both values shown, the other five `MATCH`; summary says "2 mismatch(es)"; Drafts: one *correction request* draft `PROPOSED`, recommendation `REQUEST_BL_CORRECTION`.
- [ ] **02**: `DRAFT_READY`, `PASSED`, seven `MATCH`, one *confirmation* draft, summary "No mismatch detected."
- [ ] **03**: `WAITING_DOCUMENTS`, review reason `missing_attachment`, documents panel shows the SI only, draft asks the sender for the draft BL.
- [ ] **04**: `GENERAL` / `OPERATIONAL_UPDATE`, `NO_ACTION_INFO`, `action_required: false`, priority `LOW`, Drafts tab empty. (**Complete** it by hand → `COMPLETED`.)
- [ ] **05**: `SPAM`, status `NO_ACTION_INFO`; Security card lists the phishing phrases ("storage limit", "verify your account") and the `webmail-verify.co` link, sender domain untrusted; no draft; the case also appears on the **Security** page queue.
- [ ] **06**: documents panel → the PNG attachment shows the **OCR** note "Text recovered via OCR (lower confidence)" (`attachments[].ocr = true`), the draft BL does not; every SI field carries confidence ≤ 0.90; seven fields `MATCH`; `DRAFT_READY`. Dry run on 21 Sep 2026: 7/7 values identical to the PDF version, ~29 s end to end.
- [ ] **07**: both attachments show the OCR note; seven `MATCH`; `PASSED`; ~28 s end to end. (If the API was restarted without `GOOGLE_API_KEY`, this lands in `HUMAN_REVIEW · unreadable` instead — that is the "OCR off" behaviour, not a bug.)
- [ ] **08**: `INVOICE_QUERY`, status `CLASSIFIED`, one reply draft, no seven-fields card.
- [ ] Audit tab on 01: events `EMAIL_RECEIVED → CLASSIFIED → EXTRACTED → COMPARED → DRAFT_PROPOSED`, actor `SYSTEM`/`AI`, and `AI_PROVIDER_CALL` rows show **masked** counts but never text.

## 4. Re-run determinism (2 min)

- [ ] 01 → **Compare** → same two mismatches, same values; audit gets a second `COMPARED`.
- [ ] 04 → **Classify** → same category/intent.

## 5. Complete the incomplete case (2 min)

- [ ] 03 → **Upload** → choose `testkit/03_incomplete_si_only/later/DraftBL_PSGSE9225014.txt` → status leaves `WAITING_DOCUMENTS`; compare runs → `PASSED`, `DRAFT_READY`, confirmation draft replaces the request draft.

## 6. OCR accuracy (3 min)

- [ ] Open 02 and 06 side by side (same SI content, PDF vs photo). Count the seven SI values that are identical → `n/7` = OCR field accuracy for the image (expect 7/7; ports and party names are the usual misses).
- [ ] 07: open the SI document text → all seven labels present; summary "No mismatch detected."
- [ ] Both OCR cases keep case confidence at 0.9 even when every field matches (the cap is deliberate: OCR text never earns full trust).

## 7. Policy and self-learning (5 min)

- [ ] Policies → *Operator guard · learned for you* → after steps 3–6 you have made > 20 audited actions: refresh → **learned** burst limit (`3 × your pace`) and your usual hours are shown; `GET /me/operator-profile` has `baseline.learned: true`.
- [ ] Policies → `human_review.confidence_below` → set `0.99` → **Save** (a new policy version appears in the version list) → case 08 → **Classify** → routed to review because confidence is below the new bar → set it back → Classify → `CLASSIFIED` again.
- [ ] Policies → `operator_guard.auto_draft_after` → `2` → Save → on case 01 do two quick actions (Assign to yourself, Request review) → a draft is auto-saved and a warning modal appears on the third.
- [ ] Note: drafts follow the `communication` policy (external drafts need confirmation, auto-send off). There is no classifier that retrains from your corrections; the learning in this build is the operator baseline above.

## 8. Batch and parallel runs (5 min)

- [ ] Workbench → *Batch run* → add cases 01, 02, 06, 07 (type `NS-TEST` in the picker) → **parallel = 4** → **Run agent on selected**. Results table: 01 `HUMAN_REVIEW` (paused, with a per-row **Resume**), 02 `DRAFT_READY`, 06 and 07 `DRAFT_READY` after OCR; each row shows its `ms`. The heading *Last run · agent · 4 case(s)* and the total time ≈ the slowest row (OCR ~20–30 s), not the sum.
- [ ] Same four → **parallel = 1** → Run again → total ≈ sum of the rows (roughly 2–3× longer). That difference is the parallelism.
- [ ] Same four → **Compare** (batch) → all `ok`; if any row is `error`, **Retry failed** re-runs just those.
- [ ] 01 is paused → **resume-batch**: `mark no_action all paused (1)` → 01 becomes `NO_ACTION_INFO`; then case 01 → **Retry** to bring it back to `HUMAN_REVIEW` for step 12.
- [ ] Inbox → select 04 + 05 → batch **Archive** → they leave the list (filter *archived* shows them).

## 9. Ask AI (3 min)

- [ ] 01 → *Ask AI* → "Which fields mismatch and what should I tell the customer?" → answer names `container_count` and `gross_weight_kg` with the SI/BL values and cites evidence refs `comparison:container_count`, `comparison:gross_weight_kg`.
- [ ] 05 → "Is this e-mail safe to act on?" → answer says no and cites the security signals.
- [ ] 01 → **Translate** the summary to Malay or Chinese → translated text, audit `TRANSLATED`.
- [ ] Knowledge-base hits (policy/glossary citations) require a working embedding model: `GET /rag/info` → `chunks > 0` and a search returns hits. If hits are empty, set `EMBEDDING_PROVIDER=local` (or a current Gemini embedding model) and `POST /rag/reindex`.

## 10. Share and collaborate (4 min)

- [ ] 01 → *Collaboration* → **Share internally** with `Supervisor` (`u_sup_1`) with a note → the share appears with status `SHARED`; sign in as `u_sup_1` (demo password) → bell shows the notification → **Acknowledge**.
- [ ] 01 → **Notify party** (external notify party from the SI) → share `PENDING_CONFIRMATION` → **Confirm** → in `simulate` mode it becomes `SIMULATED`/`CONFIRMING` with audit `from: <your address>`; the operator-guard modal may warn if you do this rapidly.
- [ ] Collaboration page lists both shares under *Shared by me*.

## 11. Dashboard and downloads (3 min)

- [ ] Inbox → **Export CSV** → open: the eight `[NS-TEST]` rows with mailbox column = your address.
- [ ] Inbox → **Export Excel** → same rows, one sheet.
- [ ] Workbench → **Overall report (Excel)** → 9 sheets; *Overview* KPIs include today's 8 cases, *Mailboxes* sheet lists your Outlook, *Operator activity* shows your actions, *Security* lists case 05.
- [ ] Workbench → select 01 + 02 → **Report (selected)** → two-case workbook.
- [ ] Case 01 → **Report** (per-case PDF/JSON) downloads.

## 12. Human reply (5 min)

- [ ] `simulate` first: 01 → Drafts → **Edit** the correction draft (change one sentence) → status `EDITED` → **Approve** → status `SIMULATED`; audit `NOTIFICATION_SIMULATED` with `from: <your address>`, `to: <your address>` (the sender of the test mail is you).
- [ ] Switch to live: `.env` → `EMAIL_SEND_MODE=live` → `docker compose up -d --force-recreate api` → 02 → **Approve** the confirmation draft → status `SENT`; audit `NOTIFICATION_SENT` with `mode: gmail` (the live mode's historical name), `from: <your address>`, provider id `accepted`/request id.
- [ ] Your mailbox: the reply arrives (**From** you, subject `RE: [NS-TEST 02] …`) and sits in **Sent Items** — that is the Graph `sendMail` path.
- [ ] On the draft card press **Verify delivery**: ✓ *In the sender's Sent Items* (message id shown) means the provider took it — if it is not in the recipient's inbox it is in **Spam/Junk** (a personal outlook.com sender writing "Draft BL confirmed" to Gmail is filtered on first contact; mark it "not spam" once). ✗ *Undeliverable notice* means outlook.com refused to relay it (new consumer accounts are throttled) — the NDR text is shown. Note the reply is sent **from the Outlook address**, not from the Gmail the test mail came from.
- [ ] Seeded case: open any **seeded** case (e.g. `case_email_004`) → Draft Actions shows *Replies leave from `<your Outlook>` — your connected mailbox* (the case did not arrive through a mailbox, so the approver's own one sends) → Approve → `SENT`, Delivery block shows your address. Negative check: sign in as a user **without** a connected mailbox (or disconnect yours on the Guide page) → the red note *No mailbox can send this reply: your account has no connected mailbox …* appears, **Approve & send** is disabled, and the API answers `502 no mailbox can send this reply: …` with `SEND_FAILED` on the draft (nothing left the desk).
- [ ] Set `EMAIL_SEND_MODE` back to `simulate` if you don't want later approvals to send real mail.

## 13. Cleanup (2 min)

- [ ] Guide page → **Disconnect** → card shows "not connected" → **Connect Outlook** again → consent → connected (proves the rotated refresh token and reconnect path).
- [ ] Inbox → select the eight `[NS-TEST]` cases → batch **Archive**.
- [ ] Optional: delete the test mails from your Outlook; the desk keeps its copies and audit trail.

## What each failure would mean

| Symptom | Look at |
|---|---|
| Fetch says 0 new | mails not in *Inbox* folder (Graph reads `mailFolders/inbox`), or sent to a different address than the connected one |
| 06/07 `HUMAN_REVIEW · unreadable` | OCR off: `GOOGLE_API_KEY` missing, `OCR_ENABLED=0`, policy `ai_privacy.allow_vision_ocr=false`, or the Gemini model id is retired (check `/health.llm`) |
| Drafts missing on 01/02/08 | `LLM_PROVIDER=none` — rule-based drafts still exist for BL cases; invoice replies need an LLM |
| Approve returns 502 | the case did not come through your mailbox, or the mailbox lost `Mail.Send` — Reconnect |
| Ask AI answers without KB citations | embedding provider broken → `/rag/info`, `EMBEDDING_PROVIDER=local`, reindex |
