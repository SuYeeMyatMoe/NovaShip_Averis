# AGENT.md, everything about the AI agent in NovaShip Averis

This file explains every AI part of the system: which file does what, how LangGraph and LangChain are used, how the workflow runs, how data flows today (in-memory) and with Supabase, how to test before and after Supabase, Gmail and the LLM keys are connected, how to rebuild Docker when code changes, and how a demo user tests it.

---

## 1. The two ways a case is processed

| | Classic pipeline | LangGraph agent |
|---|---|---|
| File | `backend/app/pipeline/orchestrator.py` | `backend/app/agents/graph.py` (+ `nodes.py`, `state.py`, `prompts.py`, `tools.py`, `rag.py`) |
| Used by | `POST /webhooks/email`, `/ingest/bundle`, `/cases/{id}/retry`, seed script, scorer | `POST /agent/run/{id}`, `/agent/resume/{id}`, the AI agent page and the AI Agent tab on a case |
| Human-in-the-loop | statuses only (HUMAN_REVIEW, DRAFT_READY); the person acts through the case endpoints | real pause: the graph stops at `human_review` with `interrupt()` and continues only after a decision |
| Security agent | deterministic precheck only | precheck **plus** an LLM security agent that may escalate |
| Result | identical: same modules, same audit events, same case record | identical, plus `SECURITY_AGENT_VERDICT`, `HUMAN_DECISION`, `GRAPH_COMPLETED` audit events |

Both call the same deterministic functions. The LangGraph version is the "automation with human in the loop" you asked for; the classic pipeline stays as the fast batch path (520 emails in 4.5 s) and as the scorer.

## 2. Files and what each one does

### AI nodes (LangChain-free rule modules, optional LLM inside)
| File | Part | What it does | LLM? |
|---|---|---|---|
| `backend/app/ai/security_precheck.py` | node 1 | spam phrases, sender domain, links, blocked attachment types (.exe .js ...), duplicates, flood, policy-bypass requests. Output `SAFE / SPAM / SUSPICIOUS / SECURITY_REVIEW` with evidence. | no |
| `backend/app/ai/intent_classifier.py` | node 3 | intent + category + priority + action_required from the real subject grammar (`TO CONFIRM DOCS`, `AIE - POD - CARRIER(BL#)`, `REQUEST SI`, `MISSING GR`, `_RPA_`) plus the local TF-IDF/logistic-regression classifier. | trained model for weak/ambiguous rule outcomes; LLM is the final tie-break only |
| `backend/app/ai/attachment_classifier.py` | node 4 | SI / Draft BL / Invoice / Supporting / Unknown from content and file name. | no |
| `backend/app/ai/extractor.py` | node 5 | the seven fields with label-synonym resolution and evidence (page, line, snippet). | fallback for fields the rules missed; accepted only if the quoted snippet exists in the document |
| `backend/app/core/normalizer.py` | node 5b | safe normalisation (case, whitespace, kg, integer count, port code). | no |
| `backend/app/core/comparator.py` | node 6 | **the only place MATCH / MISMATCH is decided.** Pure function, unit-tested. | never |
| `backend/app/core/recommendation.py` + `core/policy.py` | node 7 | action recommendation from the deterministic result and the versioned policy. | no |
| `backend/app/ai/summary_draft.py` | nodes 8, 9 | case summary + reply draft from the verified result. | optional polish, guarded (any dropped SI/BL value rejects the polish) |
| `backend/app/ai/assistant.py` | Ask AI | grounded Q&A, translation, share message, guardrails. | free-form questions; post-checked |
| `backend/app/ai/anomaly.py` | signals | unusual-behaviour signals with evidence (documents). | no |
| `backend/app/ai/operator_behaviour.py` | operator guard | 3 USER mutations → auto-draft (never send); burst / login-fail / share-denied / rapid-archive warnings. | no |
| `backend/app/ai/llm.py` | provider | `LLM_PROVIDER=openai / gemini / none`. Falls back to `none` silently when a key is missing. Prompts are not persisted. | |

### LangGraph layer (`backend/app/agents/`)
| File | Reference equivalent | What it does |
|---|---|---|
| `state.py` | `src/state.py` | `GraphState` TypedDict: case ids, node outputs, `route`, `needs_human`, `human_decision`, `trace`. |
| `prompts.py` | `src/prompts.py` | every prompt: `SECURITY_AGENT_PROMPT`, `INTENT_PROMPT`, `EXTRACTION_PROMPT`, `SUMMARY_PROMPT`, `DRAFT_POLISH_PROMPT`, `ASK_PROMPT`, `TRANSLATION_PROMPT`, `SHARE_MESSAGE_PROMPT`. Edit wording here. |
| `tools.py` | `src/tools/` | LangChain `@tool`s: `compare_si_bl` (deterministic), `lookup_policy`, `search_knowledge` (RAG). A model can call them, never replace them. |
| `nodes.py` | `src/nodes.py` (`Nodes` class) | one method per node: `security_precheck`, `security_agent`, `classify`, `detect_documents`, `extract`, `compare`, `summarize_and_draft`, `human_review` (calls `interrupt()`), `notify`. Customise behaviour here. |
| `graph.py` | `src/graph.py` | builds the `StateGraph`, conditional edges, checkpointer (`MemorySaver` or Postgres), `CaseAgent.run / state / resume / mermaid`. |
| `rag.py` | vector store | embeddings (Gemini, OpenAI, local hash fallback) and the store (Supabase pgvector or local `data/index.json`). |
| `create_index.py` | `create_index.py` | builds the index from `backend/data/*.md` and every case. |
| `backend/data/` | `data/` folder | the knowledge corpus: `policy_verification.md`, `glossary_shipping.md`, `ports_aliases.md`. Add your own `.md` files here. |

### API for the agent (`backend/app/api/agent_routes.py`)
`GET /agent/graph` (mermaid) · `POST /agent/run/{case_id}` · `GET /agent/state/{case_id}` · `POST /agent/resume/{case_id}` · `GET /rag/info` · `POST /rag/search` · `POST /rag/reindex` (Admin) · `GET /dashboard/fields` · `GET /dashboard/field/{field}` · `GET /security/queue` · `GET /audit`

### Frontend
`frontend/app/agent/page.tsx` (graph diagram), `frontend/app/workbench/page.tsx` (operator run console: agent run/resume, batch, CSV/XLSX, RAG info), `frontend/components/agent-panel.tsx` (AI Agent tab on a case), `frontend/app/security/page.tsx` (security agent queue + unusual operator signals), `frontend/app/verification/page.tsx` (seven-field statistics), `frontend/app/audit/page.tsx`, `frontend/app/welcome/page.tsx` (user guide).

## 3. How LangGraph and LangChain are used

**LangGraph** gives us a stateful graph with checkpoints and interrupts.

```
START -> security_precheck -> security_agent
   security_agent  --SECURITY_REVIEW--> human_review
   security_agent  --SPAM------------> summarize_and_draft -> END       (No reply needed)
   security_agent  --else------------> classify
   classify        --verification----> detect_documents
   classify        --other/no action-> summarize_and_draft
   detect_documents --both docs------> extract -> compare -> summarize_and_draft
   detect_documents --missing/unreadable--> summarize_and_draft
   summarize_and_draft --needs_human-> human_review  (interrupt: graph pauses, state checkpointed)
   summarize_and_draft --clean------> END
   human_review    --retry----------> security_precheck
   human_review    --any other------> notify -> END
```

* One thread per case (`thread_id = case_id`). `graph.get_state(config)` shows where the case is; `snap.next == ["human_review"]` and `snap.tasks[0].interrupts` hold the payload shown to the operator.
* `interrupt(payload)` in `nodes.human_review` pauses. `Command(resume=decision)` continues. The decision (`approve / edit / reject / reassign / notify_party / retry / mark_no_action / complete`) is executed through `CaseService`, so RBAC and audit apply exactly as in the UI.
* Checkpointer: `MemorySaver` by default (state lives while the API process runs). For production set `LANGGRAPH_CHECKPOINT=postgres` and `LANGGRAPH_PG_URL=postgresql://...` (Supabase connection string) and install `pip install langgraph-checkpoint-postgres "psycopg[binary]"`; paused graphs then survive restarts.
* Mermaid of the compiled graph: `GET /agent/graph` or the AI agent page.

**LangChain** is used for (1) the `@tool` wrappers in `tools.py`, (2) the embedding classes in `rag.py` (`langchain_google_genai.GoogleGenerativeAIEmbeddings`, `langchain_openai.OpenAIEmbeddings`), (3) Gemini chat/vision when `LLM_PROVIDER=gemini` or OCR is on. OpenAI chat still goes through `app/ai/llm.py` (OpenAI SDK).

### The security agent
`nodes.security_agent` reads the deterministic signals plus the email excerpt and asks the LLM (prompt `SECURITY_AGENT_PROMPT`) for `outcome, confidence, reasoning, recommended_action`. Guard: the agent may only **escalate** (SAFE -> SUSPICIOUS -> SPAM -> SECURITY_REVIEW), never downgrade a rule-based verdict. Without an LLM key it returns the rule verdict with `decided_by: rule`. Everything the agent flags is listed on the Security page (`GET /security/queue`).

### Customisation (same idea as the reference repo)
* Change a node's behaviour: edit the method in `Nodes` (`backend/app/agents/nodes.py`).
* Change wording: `backend/app/agents/prompts.py`.
* Add company knowledge: drop `.md` files into `backend/data/`, then `python -m app.agents.create_index`.
* Add a node: define the method, `g.add_node(...)` and an edge in `graph.py`, add it to `NODES` in `frontend/app/agent/page.tsx`.

## 4. RAG: Ask AI over Supabase data

1. **Corpus** = `backend/data/*.md` (policy, glossary, ports) + per-case chunks (email body, summary, seven-field comparison text, document text). Case chunks carry `case_id`, so a question on case A can never retrieve case B (`search(query, case_id=...)` filters; the SQL function does the same).
2. **Embeddings** (`EMBEDDING_PROVIDER`):
   * `gemini`: `models/text-embedding-004`, 768 dims. Key from https://aistudio.google.com/app/apikey -> `GOOGLE_API_KEY=AIzaSy...`
   * `openai`: `text-embedding-3-small`, 1536 dims. Key from https://platform.openai.com/api-keys -> `OPENAI_API_KEY=sk-proj-...`
   * `local`: hashing fallback (offline, keyword recall only). Default so the demo never breaks.
3. **Store** (`VECTOR_STORE`): `local` writes `backend/data/index.json`; `supabase` writes the `case_embeddings` table (migration `supabase/migrations/0003_vector.sql`, pgvector + `match_case_chunks` RPC). Set `vector(768)` or `vector(1536)` in the migration to match the provider.
4. **Ask AI flow**: rule branches answer the common questions directly from the case (no retrieval needed). For any other question `assistant.answer_question` retrieves the top 5 chunks, adds them to the grounded context and (if an LLM key is set) asks the model with `ASK_PROMPT`; the answer is post-checked (no un-flagged field may be called a mismatch) and cites chunk ids. Without a key it returns the best matching knowledge chunk.

Sharing, RAG, and training stay separate: Notify Party / share is operational disclosure (RBAC + preview + audit), RAG retrieves scoped chunks for Ask AI, and the only trainer is `backend/scripts/train_intent_classifier.py` on the SDOC fixture bundle. There is no "train on live inbox" path. Prompts are not persisted.

```bash
cd backend
python -m app.agents.create_index                  # local index (5 s)
EMBEDDING_PROVIDER=gemini GOOGLE_API_KEY=... VECTOR_STORE=supabase SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python -m app.agents.create_index
curl -s -X POST localhost:8000/rag/search -H "Content-Type: application/json" -H "X-User-Id: u_ops_1" -d '{"query":"what is the source of truth","sources":["policy"]}'
```

## 5. How data works right now, and with Supabase

**Now (default, `REPO_BACKEND=memory`)**
`backend/app/config.py::get_repo()` creates `MemoryRepository` and loads `supabase/seed/snapshot.json` (520 cases produced by the seed script). Everything the UI shows lives in RAM; changes are lost on restart. This is the mock API the team builds against and what the tests use.

**With Supabase (`REPO_BACKEND=supabase`)**
The same code path uses `SupabaseRepository` (`backend/app/repositories/supabase_repo.py`): every `save_case` upserts the JSONB payload into `cases` **and** the normalised child tables (`comparisons`, `comparison_fields`, `extracted_fields`, `drafts`, `case_summaries`, `action_recommendations`, `assignments`); `append_audit` inserts into the append-only `audit_events`; documents go to the `documents` storage bucket with signed URLs. Nothing in `app/ai/` or `app/agents/` touches the database directly.

Order of operations for P3: run `0001_schema.sql`, `0002_rls.sql`, `0003_vector.sql` -> load `supabase/seed/seed.sql` (or `python -m app.seed.make_seed --push`) -> set `REPO_BACKEND=supabase` + keys in `.env` -> restart the API -> `GET /health` shows `SupabaseRepository` -> `python -m app.agents.create_index` with `VECTOR_STORE=supabase`.

## 6. Keys, where to get them, what they unlock

| Variable | Get it at | Unlocks |
|---|---|---|
| `LLM_PROVIDER=openai` + `OPENAI_API_KEY=sk-proj-...` | platform.openai.com -> API keys | security agent reasoning, intent tie-break, extraction fallback, draft polish, free-form Ask AI, translation (`LLM_MODEL=gpt-4.1-mini` in the example configuration) |
| `LLM_PROVIDER=gemini` + `GOOGLE_API_KEY=AIzaSy...` | aistudio.google.com/app/apikey | Same chat uses as OpenAI, via `GEMINI_CHAT_MODEL` (default `gemini-2.0-flash`). Comparator stays code-only. |
| `OCR_ENABLED=1` + `GOOGLE_API_KEY` | aistudio.google.com/app/apikey | Gemini vision OCR on image-only PDFs, then pytesseract if installed. Unreadable still escalates. |
| `EMBEDDING_PROVIDER=gemini` + `GOOGLE_API_KEY=AIzaSy...` | aistudio.google.com/app/apikey | Gemini `text-embedding-004` for RAG |
| `EMBEDDING_PROVIDER=openai` | (uses `OPENAI_API_KEY`) | OpenAI embeddings for RAG |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` | Supabase -> Project Settings -> API | persistence, RLS, storage, JWT auth |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_ADDRESS`, `EMAIL_PROVIDER=gmail` | Google Cloud Console + one-time `backend/scripts/gmail_authorize.py` | Primary Gmail ingestion (`POST /connectors/poll`) and approved sending |

| `LANGGRAPH_CHECKPOINT=postgres`, `LANGGRAPH_PG_URL` | Supabase -> Database -> Connection string | durable paused graphs |

Without any key the whole system runs (rules only). Keys add reasoning quality; they never change the seven-field verdict.

## 7. Docker: rebuild after changes, live reload, mixed local

Full run-mode table: [README §9](README.md#9-quick-start).

```bash
# Baked images (must rebuild after code changes)
./scripts/dev.sh docker                         # or: docker compose up -d --build
docker compose build api && docker compose up -d api
docker compose build web --no-cache && docker compose up -d web

# Live reload inside Docker (bind-mount frontend + backend/app)
./scripts/dev.sh docker-dev
# docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

# After regenerating the seed
python -m app.seed.make_seed && docker compose build api && docker compose up -d api
docker compose logs -f api ; curl localhost:8000/health
```

Do not bind host `npm run dev` and Compose `web` to 3000 at the same time, or host uvicorn and Compose `api` to 8000. `./scripts/dev.sh stop` frees both.

Temporarily leave a service out while iterating:
* `./scripts/dev.sh frontend` (local Next + Docker API) or `./scripts/dev.sh backend` (local uvicorn + Docker UI).
* `docker compose up api` (only the API, no web) or `docker compose up web --no-deps`.
* Comment the service out in `docker-compose.yml` or give it `profiles: ["disabled"]` (services with a profile are skipped unless `--profile disabled` is passed). The organiser scorer already uses `profiles: ["scoring"]`.
* `docker compose stop web` / `docker compose rm -f web` removes the running container without touching the image.
* Keep the DB out of Docker entirely: Supabase is hosted, so nothing to run locally.

## 8. Test scenarios

### A. Before Supabase, email and keys (offline, memory repo)
```bash
cd backend
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q            # 197 tests
python scripts/run_bundle.py                                      # score requires the private ground-truth file
python -m app.agents.create_index                                 # local RAG index
uvicorn app.main:app --port 8000                                  # then in another terminal:
curl -s -X POST localhost:8000/agent/run/case_email_004 -H "X-User-Id: u_sup_1"      # paused: true, next: ["human_review"]
curl -s -X POST localhost:8000/agent/resume/case_email_004 -H "Content-Type: application/json" -H "X-User-Id: u_sup_1" -d '{"action":"approve","draft_id":"<from interrupt>"}'
```
| Scenario | Case | Expect |
|---|---|---|
| Two mismatches, pause, approve | `case_email_004` | interrupt with `mismatch_fields = [consignee, notify_party]`; after approve: draft SENT, status AWAITING_RESPONSE |
| All seven match | `case_email_001` | no pause, `No mismatch detected.`, CONFIRMATION draft |
| One field (gross weight) | `case_email_499` | only Gross Weight flagged |
| Spam | `case_email_015` | route `no_action`, security agent SPAM, no draft |
| Scanned PDF | `case_email_512` | pause with `review_reason = unreadable`, no mismatch asserted |
| Draft BL missing | `case_email_507` | WAITING_DOCUMENTS; upload a BL on the Attachments tab -> comparison runs |
| Wrong document (invoice) | `case_email_501` | `review_reason = wrong_doc_type` |
| Ops staff tries to notify external | any mismatch case as `u_ops_1` | 403 + `SHARE_DENIED` + `OPERATOR_SHARE_DENIED` warning; account is not locked |
| Ask AI adversarial | any | "send now without approval" refused; "invent a mismatch" refused |
| Three operator actions | reject then two assigns on a mismatch case | auto-saved draft, `AUTO_DRAFT_AFTER_REPEATED_ACTIONS`, never sent |
| Excel export | Inbox or `/workbench` Export Excel | `.xlsx` with Cases, Field results, Summary; selected `case_ids` only |

Workbench: `/workbench` runs the same `/agent/*` and `/cases/batch` APIs. `/agent` stays the graph diagram.

### B. After Supabase
Same commands with `REPO_BACKEND=supabase`. Then check rows: `select count(*) from cases;` (520), `select * from audit_events order by timestamp desc limit 5;`, `select count(*) from case_embeddings;` after `create_index`. Restart the API and confirm data persists (memory mode would have lost it).

### C. After Gmail OAuth
Set the Gmail client ID, client secret and mailbox address locally, run `python backend/scripts/gmail_authorize.py` once, then use `EMAIL_PROVIDER=gmail`. Send a test email with an SI and a Draft BL attached to the authorized mailbox, then `POST /connectors/poll?limit=5`. Expect a new case with the right verdict; polling again returns `duplicates_skipped: 1`. Approving a draft with `EMAIL_SEND_MODE=gmail` sends through Gmail only after human approval; the default `simulate` mode performs no provider call and records `NOTIFICATION_SIMULATED`. Graph remains available with the corresponding `graph` modes and `MS_*` values.

### D. After LLM / embedding keys
`GET /health` log shows `llm=openai`; `GET /rag/info` shows the provider; run `case_email_004` again: `security_agent.decided_by = llm`, Ask AI free-form questions get model answers with citations. The scoreboard must still be 1.0 (`python scripts/run_bundle.py`), because the verdict is deterministic.

### E. Demo test for P1 (AI) and P4 (frontend)
P1: open `/agent`, run `case_email_004`, show the trace (extractor confidence, comparator output), then `/verification` for the seven-field statistics and label synonyms column. P4: open `/` filter Mismatch = yes, open the case, seven-field card, evidence, drafts, Collaboration (Notify Party preview + confirm), Audit. Full script in `docs/DEMO_SCRIPT.md`.

### F. How a user can test it
Send them the deployed URL and the Guide page (`/welcome`). They pick a role in the header, follow the six steps, and try the listed cases. To test with their own documents: Attachments tab -> Upload (SI or BL) on `case_email_507`, or ask P3 to `POST /webhooks/email` with their files. Their feedback goes into the pilot table in README section 10.
