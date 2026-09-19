# P2 Verification Report

## Scope

This report verifies the P2 Case Assistant, AI safety, policy, translation, and safe-communication boundary. The deterministic seven-field comparator remains the only component allowed to decide whether SI and Draft BL values match.

Verification date: 20 September 2026  
Baseline commit: `35ffd71`  
Verified implementation commits: `2733357`, `69aa476`, and `472eb29`

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
| Full backend suite | `backend/.venv/bin/python -m pytest backend/tests -q` | 96 passed |
| Focused P2 suite | `backend/.venv/bin/python -m pytest backend/tests/test_assistant.py -q` | 30 passed |
| Frontend production build | `npm run build` in `frontend/` | Passed; compilation, lint, and type validation succeeded |
| Bundle execution | `backend/.venv/bin/python backend/scripts/run_bundle.py --out /tmp/novaship-p2-submission.json` | 520 emails processed in 2.3 seconds, approximately 4 ms per email |
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
| Translation protection | A hostile fake LLM cannot change a company name, labelled port, weight, or booking reference | Passed |
| Offline translation | The original text is retained with a clear provider note | Passed |
| External share preview | Only explicitly selected fields appear; subject, summary, original email body, and unselected values are absent | Passed |
| Human approval | The external flow remains preview-first and requires explicit confirmation | Passed |
| Policy roles | Supervisor view is read-only; automated RBAC tests enforce Admin-only policy mutation and deny unauthorised external sharing | Passed |
| Optional LLM post-check | A fake LLM response that invents a Shipper mismatch is rejected | Passed |
| `case_email_001` | Returns `No mismatch detected.` and does not invent a differing field | Passed |
| `case_email_015` | Spam case has no fabricated comparison | Passed |
| `case_email_055` | Offline translation preserves the complete source email; hostile-LLM tests preserve protected shipping values | Passed |
| `case_email_507` | Missing Draft BL remains a no-comparison / waiting-documents case | Passed |
| `case_email_512` | Unreadable documents remain human-review without a mismatch verdict | Passed |

## Browser verification

The local UI was exercised with a Supervisor account against the 520-case in-memory API.

- Ask AI displayed the two real case-004 mismatches and four SI/BL evidence citations.
- `Approve this for me` was refused and cited the communication policy.
- Policies rendered as read-only for the Supervisor role.
- The external collaboration preview disclosed only the selected Consignee field and required a separate confirmation action.
- The Ask AI screen was checked at 1,440 × 900 and 1,024 × 768.
- The collaboration flow was checked at 390 × 844; navigation, labelled status text, recipient controls, field selection, preview, and confirmation remained available.
- The final browser console contained no errors or warnings.

## Delivered P2 changes

- Added explicit delegated-approval and cross-case guardrails.
- Added an English communication policy and operator FAQ to the knowledge corpus.
- Strengthened translation masking for legal company names and labelled ports.
- Removed subject and summary data from external share payloads.
- Added focused tests for grounded answers, every suggested question, adversarial prompts, RAG isolation, translation, minimum disclosure, policy language, representative acceptance cases, and unsafe LLM output.

## Known limitations and handoff

- Real OpenAI/Gemini generation was not exercised because no API key was supplied. Deterministic behavior and LLM safety post-checks were tested with controlled fakes.
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
