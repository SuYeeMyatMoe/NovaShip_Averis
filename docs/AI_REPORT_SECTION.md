# AI system and evaluation

## Architecture

NovaShip uses a hybrid, evidence-first architecture. LangGraph provides the stateful case workflow, checkpointed per case, and pauses the graph at `human_review` when an operator must decide. LangChain is limited to tool wrappers and embedding integrations. The seven-field shipping verdict is never delegated to a language model.

| Component | Implementation | Role of AI | Safety boundary |
|---|---|---|---|
| Security | `app/ai/security_precheck.py`, then the LangGraph security agent | An OpenAI model explains or escalates deterministic security signals | The model cannot downgrade a rule verdict |
| Intent classification | rules plus a TF-IDF/logistic-regression classifier, with an optional LLM last tie-break | Classifies BL comparison, SI request, invoice query, general, or spam | Missing/incompatible model artifacts fall back to deterministic rules |
| Attachment handling | `app/ai/attachment_classifier.py`, `app/readers/document_reader.py` | Classifies supported attachments; document bytes are parsed, never executed | Executable types are blocked; unreadable/wrong documents go to human review |
| Seven-field extraction | `app/ai/extractor.py` | Label synonyms first; optional LLM only fills a missed value when its quoted snippet exists in the document | Blank values are not invented; every value carries document, page, line, snippet, and label evidence |
| Comparison | `app/core/normalizer.py`, `app/core/comparator.py` | None | A pure deterministic function is the only component allowed to decide MATCH/MISMATCH |
| Recommendation and communication | policy, summary, and draft nodes | Optional wording polish | External communication is drafted only and requires authorised human approval |
| Retrieval | Gemini/OpenAI/local embeddings with local or Supabase vector storage | Grounds free-form Ask AI responses in policy and case-scoped evidence | Case filters prevent cross-case retrieval; responses are post-checked |

The graph path is:

```text
START -> security_precheck -> security_agent -> classify
      -> attachment_classifier -> document_extractor
      -> seven_field_comparator -> summary_and_draft
      -> human_review (when required) -> notify -> END
```

Spam exits after summary, and missing, wrong-type, or unreadable documents route to human review without asserting a mismatch. One LangGraph thread is used per case, so an interrupted decision can resume against the same state. Development uses an in-memory checkpointer; production should use the Postgres checkpointer so paused cases survive restarts.

## Trained intent classifier

The local intent classifier uses word and character TF-IDF features with balanced logistic regression. The data split is deterministic (`seed=42`) and template-group-aware, which keeps lookalike subject templates out of opposite partitions.

| Measure | Result |
|---|---:|
| Total labelled emails | 520 |
| Training set | 370 (71.15%) |
| Untouched test set | 150 (28.85%) |
| Rule baseline accuracy / macro-F1 | 1.000 / 1.000 |
| Trained model accuracy / macro-F1 | 1.000 / 1.000 |
| Runtime hybrid accuracy / macro-F1 | 1.000 / 1.000 |

Confusion-matrix label order is BL comparison, SI request, invoice query, general, spam. The test-set diagonal is `[71, 37, 22, 7, 13]`; every off-diagonal cell is zero. This is a strong baseline for the supplied APRIL dataset, but not proof of generalisation to new senders or unseen writing styles. The fixed test partition must remain untouched, and future evaluation should add newly labelled real emails as a separate out-of-time test set.

The runtime order is rules, then the trained model for weak or ambiguous rule outcomes, then the LLM only if confidence is still low. Strong deterministic matches remain deterministic. The model artifact records its training fingerprint and dependency versions and is copied into the API Docker image.

## Verification results, updated 20 September 2026

| Check | Result |
|---|---|
| Full automated suite | 121 passed |
| Organiser bundle scoreboard | Recorded result: 1.0000 across all 520 cases; not locally recomputed because the private ground truth is absent |
| `pytest -k container_3_vs_4` | Covered by the full suite |
| `pytest -k all_seven_match` | Covered by the full suite |
| Three real APRIL document pairs | TXT (`email_004`), XLSX (`email_005`), and PDF (`email_059`) extracted all seven SI and BL fields; no missing synonym found |
| Gemini RAG | Reindexed 1,418 chunks at 768 dimensions; policy search returned grounded hits |

Live LangGraph runs with the OpenAI provider enabled produced the following results:

| Case | Outcome | Evidence from trace |
|---|---|---|
| `case_email_004` | `HUMAN_REVIEW`, paused | SAFE by LLM; seven extraction/comparison nodes; consignee and notify-party mismatches |
| `case_email_001` | `DRAFT_READY`, completed | SAFE by LLM; all seven fields matched; confirmation draft prepared |
| `case_email_015` | `NO_ACTION_INFO`, completed | SPAM by LLM; no-action route; no document comparison attempted |
| `case_email_512` | `HUMAN_REVIEW`, paused | SAFE by LLM; scanned PDF marked unreadable; no mismatch asserted |

The security explanations cited the email content, sender/domain characteristics, shipment references, and attachment types. They did not contradict the deterministic outcomes, so no prompt wording change was required.

## Deployment and reproducibility

Docker is the reproducible execution environment, not the training algorithm. Training runs ordinary scikit-learn inside Python 3.11; the container supplies the pinned dependencies and lets machines without a local Python setup reproduce the artifact. Training explicitly disables the LLM and does not need OpenAI or Google keys.

For deployment, only `backend/models/intent_classifier.joblib` is required for inference. The metrics and split manifests are audit artifacts. Retraining also needs the labelled organiser dataset, which is intentionally not required by the running service. If the artifact is absent or incompatible, ingestion continues using rules.

The Compose deployment fixes bundle and seed locations to `/data/bundle` and `/data/seed/snapshot.json`; otherwise host-relative `.env` values can override the paths inside the container and make valid attachments appear unreadable. An embedding-provider change also requires a full RAG reindex because local, Gemini, and OpenAI vectors have different dimensions.
