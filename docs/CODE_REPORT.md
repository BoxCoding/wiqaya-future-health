# Wiqaya code report

Snapshot of the repository on 2026-11-19 (updated after the review feedback: care loop, EHR interfaces, bottleneck analyst). Wiqaya is a working prototype for the Future Health
Digital Innovation Challenge (Population Health). All data is synthetic.

| | |
| --- | --- |
| Live demo | https://wiqaya-242709395679.me-central1.run.app (Google Cloud Run, me-central1, no login) |
| Stack | Python 3.12 · FastAPI · NumPy · vanilla JavaScript · Gemini `gemini-3.1-flash-lite` |
| Tests | 56 unit and API tests, all passing (about 2.5 s, no model or key needed) |
| End-to-end | 81 Selenium tests in the separate `wiqaya-qa` repository (74 non-AI tests run and passed on 2026-11-19) |
| Runtime dependencies | 4: `fastapi`, `uvicorn`, `numpy`, `certifi` |

## 1. Structure and size

Python, counted without blank and comment lines.

| File | Lines | Code | Functions | Longest function (lines) | Job |
| --- | ---: | ---: | ---: | --- | --- |
| `app/agents.py` | 354 | 301 | 19 | `population_query` (34) | Multi-agent copilot: supervisor, planner, verifier, writers, bottleneck analyst, safety agent |
| `app/loop.py` | 267 | 226 | 15 | `simulate` (61) | Screening-to-care loop: stages, targets, legal transitions, clinician closure, barriers, strategies, worklist |
| `app/interop.py` | 201 | 173 | 10 | `apply_resource` (45) | FHIR R4 ingest and export, HL7 v2 ORU^R01 parsing and ACKs |
| `app/engine.py` | 182 | 155 | 16 | `__init__` (27) | Builds the population once; scores, plans, views, query tool |
| `app/main.py` | 246 | 175 | 27 | `feed_demo` (12) | FastAPI routes, validation, AI rate limit, ingest token check, static UI |
| `app/llm.py` | 107 | 87 | 8 | `chat` (22) | Gemini client; Ollama only on local runs; fails closed |
| `app/data.py` | 99 | 83 | 5 | `_person` (41) | Synthetic Abu Dhabi-like population with hidden ground truth |
| `app/model.py` | 90 | 70 | 9 | `RiskModel.__init__` (13) | Bias-corrected risk model, bootstrap intervals, T-learner uplift |
| `app/outreach.py` | 77 | 64 | 5 | `compose` (34) | Approved Arabic/English templates, privacy rules, slot times |
| `app/safeguards.py` | 66 | 51 | 6 | `_row` (14) | Segment table, reach gate, small-cell suppression |
| `app/rules.py` | 64 | 53 | 4 | `care_gaps` (33) | Deterministic care-gap rules for 7 pathways |
| **Backend total** | **1,665** | **1,317** | **115** | | |
| `tests/` (2 files) | 266 | 197 | 30 (incl. fixtures) | | Unit, API and agent tests |

| Front end | Lines | Notes |
| --- | ---: | --- |
| `static/app.js` | 954 | Single-page app, no framework or build step; every server and model string goes through `esc()` |
| `static/styles.css` | 676 | Light and dark themes, responsive layout |
| `static/index.html` | 60 | Shell and tab structure |

Every module has one job. The longest function is the care-loop simulation (61 lines), which replays eight weeks of synthetic EHR events. The longest functions
(`_person`, `compose`, `population_query`, `care_gaps`) are straight-line rule code, not deep nesting.

## 2. Architecture

```
Browser (vanilla JS, EN/AR)
   │  JSON over HTTPS
FastAPI  app/main.py  ── validation · AI rate limit (20/min/client) · no-cache UI
   ├── Engine  app/engine.py
   │     ├── rules.py      care gaps (placeholder schedule)
   │     ├── model.py      risk model (screened-only) + invitation-lift model
   │     ├── outreach.py   household messages from approved templates
   │     └── safeguards.py reach gate + small-cell suppression
   └── Agents  app/agents.py ──► llm.py ──► Gemini (Ollama on local runs only)
```

Interactive diagram with source links: `docs/architecture/wiqaya-runtime.html`.

Design rules the code enforces:

- **The model never states a fact.** Code writes every factual sentence; the model returns JSON
  with a fact-free lead and the keys of the sentences to use. `lead_check` rejects digits, number
  words, names, places, claims and alarmist words; one revision, then the rule-based answer.
- **Model-proposed filters must be in the question.** `_evidence` drops any filter the user's
  own words do not support.
- **The app always answers.** Every LLM failure, timeout or invalid output returns `None`, and
  the caller falls back to deterministic text.
- **Patient-facing messages are never generated.** They are approved templates filled in code.

## 3. API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | Liveness |
| GET | `/api/overview` | Headline counts |
| GET | `/api/plan/{centre}` | 404 on unknown centre |
| GET | `/api/person/{pid}` | 404 out of range; consent-withheld fields and others' private messages removed |
| POST | `/api/decisions` | Pydantic-validated; pathway must be one recommended for that person |
| GET | `/api/safeguards?dimension=` | 422 on unknown dimension |
| GET | `/api/agent/status` | Provider, model, online |
| POST | `/api/agent/ask` | 3–400 characters; rate limited |
| GET | `/api/agent/brief/{pid}` | Rate limited |
| GET | `/api/outcomes` | Policy comparison, bias check, calibration |
| GET | `/api/loop` | Funnel, overdue counts, worst stage, delay by barrier and centre (groups under 10 hidden) |
| GET | `/api/loop/worklist?stage=` | Overdue episodes, abnormal first, each with an explained next step; 422 on unknown stage |
| GET | `/api/episodes/{id}` | Episode timeline, status, barriers, strategy |
| POST | `/api/episodes/{id}/close` | Clinician sign-off; 409 when the loop rules forbid it |
| POST | `/api/episodes/{id}/outreach` | Coordinator approves or dismisses the suggested step |
| GET | `/api/agent/bottlenecks` | Gemini bottleneck analyst; rate limited |
| POST | `/api/fhir/Bundle` | FHIR R4 ingest; bearer token; one OperationOutcome issue per entry |
| POST | `/api/hl7` | HL7 v2 ORU^R01 ingest; bearer token; returns an ACK (AA or AE) |
| GET | `/api/fhir/Patient/{id}/$everything` | Consent-filtered FHIR Bundle with a Task per episode |
| POST | `/api/feed/demo` | Generates a lab result and runs it through the real HL7 path; rate limited |

## 4. Testing

| Suite | Count | What it covers |
| --- | ---: | --- |
| `tests/test_core.py` | 16 | Care-gap rules, consent, outreach privacy, suppression, plan capacity, outcomes, API round trip, consent-withheld fields, private messages |
| `tests/test_agents.py` | 30 | Rule parsing, verifier, provider matrix (parametrised), `lead_check`, revision then fallback, offline answers |
| `tests/test_loop.py` | 10 | Stage order, closure rules, forward-only transitions, worklist, small-group suppression, HL7 and FHIR ingest, consent-filtered export, offline bottleneck analyst, ingest token and clinician sign-off over the API |
| `wiqaya-qa` (separate repo) | 81 | Selenium page objects across every tab, including 9 care-loop tests; AI tests assert invariants, not wording |

Tests run with `WIQAYA_HOUSEHOLDS=1500` and no model, so they are fast and deterministic.
Line coverage is not measured (no coverage tool is installed).

## 5. Code quality review (this round)

Duplicates removed, behaviour unchanged, all tests passing:

| Where | Before | After |
| --- | --- | --- |
| `app/agents.py` | `ask()` and `brief()` built the same reply dict | One `_reply()` helper |
| `app/engine.py` | High-risk threshold `0.15` written again | Reuses `safeguards.HIGH_RISK` |
| `app/llm.py` | `provider()` resolved twice per call | Resolved once |
| `static/app.js` | POST + JSON headers copied twice | `post()` helper |
| `static/app.js` | Agent-stage markup copied twice | `stages()` helper |
| `static/app.js` | Required-fact list defined twice | One `REQUIRED` table |
| `README.md` | Said 29 tests | 46 tests; deploy section added |

## 6. Security and privacy

A full security audit (source review, independent verifiers) found no confirmed vulnerability and
two privacy leads; a later pass added a third. All three are fixed, each with a regression test:

| Issue | Fix | Commit |
| --- | --- | --- |
| `/api/person` returned genomic variant, family-history age and distance when consent withheld them | `public_person()` drops those fields | `fa46c56` |
| Safeguard averages published over sub-groups under 10 people | `_cell_mean()` hides them | `fa46c56` |
| A person's page showed every household member's private messages | Only shared messages and the person's own private ones | `24d7dd3` |
| Unknown `WIQAYA_LLM` values could still call Gemini | Unknown values fail closed to "off" | `b9276b8` |

Controls in place: the Gemini key is sent only in a request header and lives in Secret Manager in
production (never in the image or git; `.env` is git-ignored and Docker-ignored); all rendered
strings are HTML-escaped; inputs are validated by Pydantic and range checks; the AI endpoints are
rate limited; counts under 10 are suppressed.

## 7. Deployment

`Dockerfile` (python:3.12-slim) and `deploy/cloudrun.sh`, which enables the APIs, stores the key in
Secret Manager, and deploys with `WIQAYA_ENV=production` (Ollama never used), 1 GiB, scale to zero,
max 1 instance.

## 8. Known limitations and next steps

| Item | Why it matters | Suggested fix |
| --- | --- | --- |
| Decisions, episodes and rate limit are in process memory | Lost on restart; limits only hold with one instance | Episodes as FHIR Tasks in the EHR, PostgreSQL for decisions, Redis for limits |
| No login | Anyone can record a decision or sign off an episode on the public demo; the clinician name is typed | DoH single sign-on with clinician and coordinator roles before any pilot |
| One shared ingest token | Any holder of the token can push events | Mutual TLS from the interface engine, per-source tokens |
| Local pathway codes | Not yet interoperable terminology | Map to DoH-approved LOINC and SNOMED CT sets |
| Dependencies unpinned | A new release can change behaviour at build time | Pin versions in `requirements.txt` |
| No Content-Security-Policy header | Defence in depth against script injection | Add a CSP middleware |
| Rate limit keys on client IP | Behind a proxy many users can share one IP | Key on a trusted forwarded header |
| Care-gap intervals are placeholders | Not the DoH-approved schedule | Load the clinical leads' versioned catalogue |
| Arabic templates are drafts | Not clinically or natively reviewed | Native-speaker and clinical review |
| Line coverage not measured | Untested branches are invisible | Add `pytest-cov` to the dev requirements |
