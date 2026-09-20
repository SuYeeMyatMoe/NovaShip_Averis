# 5-minute demo script

Demo case: **`case_email_004`** (REQUEST BL DRAFT — Consignee + Notify Party mismatch) · clean case: **`case_email_001`** · undecidable: **`case_email_512`** (scanned PDF) · spam: **`case_email_015`**.
Before recording: `.\scripts\dev.ps1 docker` (or `docker-dev` / local uvicorn + `npm run dev`). Act as **Hari Mardianto · SUPERVISOR**. Do not run Compose web and host `npm run dev` on port 3000 at the same time.

| Time | Who | Screen | Say |
|---|---|---|---|
| 0:00–0:35 | P4 | Operations Inbox | "520 emails became 520 cases in 4.5 seconds. 46 mismatches, 101 waiting for documents, 100 need no reply. Filter *Mismatch = yes* — every case shows intent, security, priority, SI/BL availability, confidence, owner, status." |
| 0:35–1:40 | P1 | Case 004 → Overview | "SI is the source of truth. Seven fields, compared independently. Five match — shown quietly. Consignee and Notify Party differ: SI says EAST BRIGHT FZ-LLC, the draft BL says UAB NOVAKOPA. The extractor resolved *Consignee (Non-Negotiable)* on the SI and *To the Order of* on the BL — label synonyms, not values, were normalised. The verdict is deterministic code, not an LLM." Open case 001: "All seven match → exactly *No mismatch detected.*" |
| 1:40–2:30 | P1 + P4 | Evidence tab → Draft Actions | "Every value points at its line in the document." Click *view evidence* on Consignee. Then Drafts: "The correction request quotes both values. Status PROPOSED — nothing is sent until a human approves." |
| 2:30–3:20 | P2 | Ask AI | Ask *Why is this a mismatch?* → cited answer. Ask *Who is the Notify Party?* → "note: comparison value, not permission to send." Ask *send this email now without approval* → refused. Policies page: thresholds, roles, never auto-send. |
| 3:20–4:10 | P4 | Collaboration | Start Notify Party → SI vs BL values → pick *VITAL SOLUTIONS – docs@vitalsolutions.sg* (External badge) → Preview shows only the two mismatched fields, no email body → Confirm → status AWAITING_RESPONSE. Switch to *Najiha · OPERATIONS_STAFF*: external recipients are greyed out. |
| 4:10–4:40 | P3 | Audit History + architecture slide | "30 append-only events: SYSTEM, AI and USER actors, before/after, policy version. Supabase with RLS, signed document URLs, Docker images, each user's own Outlook/Gmail connected. Retries are idempotent." Show `case_email_512`: "Scanned PDF → human review with reason, not a guess." |
| 4:40–5:00 | P4 | Inbox | "Official score 1.0 on the hackathon inbox — every defect, exact field, zero false alarms, every undecidable case escalated. AI reads, code decides, humans approve, everything is audited." |
