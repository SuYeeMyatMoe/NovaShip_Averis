# Documentation index

For deployment and handoff, read these in order:

1. [BACKEND_HANDOFF.md](BACKEND_HANDOFF.md) — implemented and validated backend state.
2. [DEPLOYMENT.md](DEPLOYMENT.md) — Vercel import, environment values and deployed smoke checks.
3. [DEMO_AND_LIVE_MODES.md](DEMO_AND_LIVE_MODES.md) — what demo mode means and the path to production.
4. [CONTRACTS.md](CONTRACTS.md) — API/data contracts.
5. [DEMO_SCRIPT.md](DEMO_SCRIPT.md) — presentation flow.
6. [P4_EVIDENCE_HANDOFF.md](P4_EVIDENCE_HANDOFF.md) — frontend/evidence handoff.
7. [AI_REPORT_SECTION.md](AI_REPORT_SECTION.md) — AI/reporting context.

The repository is prepared as one Vercel Services project: `frontend/` is Next.js, `backend/` is FastAPI, and root `vercel.json` routes FastAPI under `/api`.
