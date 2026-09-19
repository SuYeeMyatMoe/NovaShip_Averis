# P2 Verification Report

## Scope

This report verifies the P2 Case Assistant, AI safety, policy, translation, and safe-communication boundary. The deterministic seven-field comparator remains the only component allowed to decide whether SI and Draft BL values match.

Verification date: 20 September 2026

Baseline commit: `35ffd71`

Verified branch: `codex/p2-completion`

## Environment

| Component | Verified configuration |
|---|---|
| Python | 3.12.13 in `backend/.venv` |
| Node.js | 24.15.0 |
| npm | 11.12.1 |
| Repository | `MemoryRepository`, 520 seeded cases |
| LLM | Disabled (`LLM_PROVIDER=none`) |
| Embeddings | Local hashing, 256 dimensions |
| Vector store | Local JSON index |

## Automated verification

| Check | Command | Result |
|---|---|---|
| Full backend suite | `backend/.venv/bin/python -m pytest backend/tests -q` | 119 passed in 6.41 seconds |
| Focused P2 suite | `backend/.venv/bin/python -m pytest backend/tests/test_assistant.py -q` | 52 passed in 3.67 seconds |
| Frontend production build | `npm run build` in `frontend/` | Passed; compilation, lint, and type validation succeeded |
| Bundle execution | `backend/.venv/bin/python backend/scripts/run_bundle.py --out /tmp/novaship-p2-submission.json` | 520 emails processed in 1.7 seconds, approximately 3 ms per email |
| RAG rebuild | `backend/.venv/bin/python -m app.agents.create_index` from `backend/` | 19 knowledge chunks plus 1,408 case chunks; 1,427 total |
| Patch integrity | `git diff --check` | Passed |

The organiser ground-truth file is intentionally absent from this checkout. The bundle runner therefore generated a complete 520-email submission but could not recompute the recorded `1.0000` scoreboard. This report does not present the historical score as a fresh local measurement.

The locally generated case-status totals were:

| Status | Count |
|---|---:|
| `DRAFT_READY` | 63 |
| `CLASSIFIED` | 200 |
| `WAITING_DOCUMENTS` | 101 |
| `HUMAN_REVIEW` | 56 |
| `NO_ACTION_INFO` | 100 |

## P2 acceptance matrix

| Scenario | Evidence | Result |
|---|---|---|
| `case_email_004`: explain mismatch | UI and automated test return only Consignee and Notify Party, with SI and BL citations | Passed |
| Ten suggested Ask AI questions | Parameterized test requires every answer to be grounded, non-empty, and cited | Passed |
| Dangerous requests | Send-now, approval bypass, delegated approval, invented values, and other-case requests return `refused=true` with policy evidence | Passed |
| Cross-case isolation | A search scoped to case 004 cannot return case 001 chunks | Passed |
| Knowledge retrieval | Communication policy and operator FAQ are indexed; approval and THC queries retrieve the intended knowledge | Passed |
| Translation protection | Full labelled shipping values are masked; mixed-case/FZE company forms are protected; missing, duplicated, or reordered protection tokens cause a safe return to the original text | Passed |
| Offline translation | The original text is retained with a clear provider note | Passed |
| External share preview | Only explicitly selected fields appear; an explicit empty selection remains empty; subject, summary, original email body, and unselected values are absent | Passed |
| Human approval | Confirmation sends the exact frozen message and payload under the same share ID; repeated confirmation is idempotent | Passed |
| Policy roles | Supervisor view is read-only; automated RBAC tests enforce Admin-only policy mutation and deny unauthorised external sharing | Passed |
| Optional LLM post-check | Unsupported mismatch/match verdicts, invented field values, POL/POD/Weight aliases, and active or passive send/approval claims are rejected across multiple phrasings; supported verdicts remain accepted | Passed |
| Current-case aliases | `case_email_004`, `case-email-004`, and `case 004` resolve to the current case without weakening cross-case isolation | Passed |
| `case_email_001` | Returns `No mismatch detected.` and does not invent a differing field | Passed |
| `case_email_015` | Spam case has no fabricated comparison | Passed |
| `case_email_055` | Offline translation preserves the complete source email; hostile-LLM tests preserve protected shipping values | Passed |
| `case_email_507` | Missing Draft BL remains a no-comparison / waiting-documents case | Passed |
| `case_email_512` | Unreadable documents remain human-review without a mismatch verdict | Passed |

## Browser verification

The local UI was exercised with Operations and Admin accounts against the 520-case in-memory API.

- Ask AI displayed the two real case-004 mismatches and four SI/BL evidence citations.
- `Approve this for me` was refused and cited the communication policy.
- Policies rendered with disabled controls and save action for Operations; the Admin view exposed the editable controls.
- The Operations dashboard no longer requests the Admin-only audit endpoint, eliminating the previous `403` console error.
- The external collaboration preview preserved a custom message, disclosed no comparison values when the user explicitly cleared every field, and required a separate confirmation action. Confirmation finalized the same frozen share record as `SENT`.
- The internal collaboration preview used the internal label and completed through the same preview/confirmation record without an external-notification claim.
- The Ask AI screen was checked at 1,440 × 900 and 1,024 × 768.
- The collaboration flow was checked at 390 × 844; navigation, labelled status text, recipient controls, field selection, preview, and confirmation remained available.
- The final P2 browser pass contained no application errors or warnings. A separate fresh dev-server session recorded only the pre-existing missing `/favicon.ico` asset, unrelated to P2 behavior.

## Delivered P2 changes

- Added explicit delegated-approval and cross-case guardrails.
- Added deterministic post-validation for LLM field verdicts, field values, and completed-action claims.
- Added an English communication policy and operator FAQ to the knowledge corpus.
- Strengthened translation masking for complete labelled values and additional legal company forms, with fail-safe token integrity and ordering checks.
- Removed subject and summary data from external share payloads.
- Made explicit empty field selections disclose no comparison fields.
- Made preview confirmation finalize the same frozen share record and remain idempotent on retry.
- Prevented Operations dashboards from calling the Admin-only audit endpoint.
- Corrected internal collaboration preview language so it does not imply an external notification.
- Added focused tests for grounded answers, every suggested question, adversarial prompts, RAG isolation, translation, minimum disclosure, exact preview confirmation, policy language, representative acceptance cases, and unsafe LLM output.
- Delivered the English safety and policy presentation at `docs/NovaShip_P2_Safety_Policy_Deck_v2.pptx`.

## Known limitations and handoff

- Real OpenAI/Gemini generation was not exercised because `OPENAI_API_KEY` and `GOOGLE_API_KEY` were unset. Deterministic behavior and LLM safety post-checks were tested with controlled fakes.
- Real Supabase pgvector was not exercised; the local store verified the same case-filter contract. Supabase and migration validation remain a P3 responsibility.
- Real Outlook delivery was not exercised. External delivery remains simulated until P3 enables and validates Microsoft Graph.
- The organiser score cannot be recomputed without the private ground-truth file.
- `npm install` reports two dependency vulnerabilities in the current Next.js dependency tree (one high and one critical). A controlled framework upgrade should be handled separately because it is outside P2 behavior and may introduce breaking changes.

## Reproduction

```bash
# Backend
backend/.venv/bin/python -m pytest backend/tests -q
backend/.venv/bin/python backend/scripts/run_bundle.py --out /tmp/novaship-p2-submission.json

# RAG
cd backend
.venv/bin/python -m app.agents.create_index
cd ..

# Frontend
cd frontend
npm run build
cd ..

# Repository checks
git diff --check
git status --short
```
