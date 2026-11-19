# Wiqaya: predictive prevention pathways (prototype)

Future Health Digital Innovation Challenge, Population Health. Wiqaya finds children, adults and
households with care gaps or elevated risk, and fills each primary-care centre's real weekly slots
with the households where an invitation does the most good. Every invitation is then tracked through
booking, result, referral and diagnosis to a clinician-signed closure, so no abnormal result is lost to
follow-up. Every flag shows the consented data behind it.

**All data is synthetic** (about 41,700 people in 12,000 households). No real records are used.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8099     # open http://localhost:8099
echo 'GEMINI_API_KEY=your-key' > .env          # without a key, a local run uses Ollama
.venv/bin/python -m pytest -q                  # 66 tests, no model or key needed
```

`WIQAYA_HOUSEHOLDS` sets the population size (default 12000; startup takes about 5 s).

## Deploy (Google Cloud Run)

```bash
PROJECT_ID=<your-project> deploy/cloudrun.sh   # reads GEMINI_API_KEY, WIQAYA_INGEST_TOKEN and WIQAYA_SESSION_SECRET from .env into Secret Manager
```

Live demo: https://wiqaya-242709395679.me-central1.run.app. Code report: `docs/CODE_REPORT.md`.

## Sign-in, roles and audit

Every `/api` route except `/health` and `/api/auth/*` needs a signed-in user. Passwords are hashed with scrypt; the
session is an HttpOnly, SameSite=Strict, HMAC-signed cookie (Secure outside local runs) that expires after 8 hours.
Sign-in attempts are limited to 10 per 5 minutes per address.

| Role | Can |
| --- | --- |
| Clinician | Individual records, AI case briefs, care-gap decisions, clinician sign-off of care episodes |
| Care coordinator | Individual records, follow-up worklist, outreach approval, patient-reported barriers |
| Population planner | Population views only: no names, no individual records (the API returns 403) |
| Privacy officer | Population views plus the audit log |

**Demo access** (shown on the login page): `dr.amna`, `sara.coord`, `omar.planner`, `privacy.admin`, password
`Wiqaya-Demo-2026`, or **Continue as guest** with any role. Guests are labelled "(guest)" on every sign-off and in the
audit log. Outside a demo set `WIQAYA_GUEST=0` and `WIQAYA_DEMO_USERS=0`.

The **audit log** records every sign-in (and failed attempt), record view, worklist view, decision, sign-off,
approval, reported barrier, FHIR export, inbound EHR message, AI request and refused access, with user, role and
address. It is append-only and hash-chained: each row stores the hash of the previous one, so an edited or deleted row
shows as a broken chain. **Persistence:** decisions and every care-episode change are saved to SQLite (`WIQAYA_DB`) and
replayed on top of the simulated history at startup.

## Screening-to-care loop and EHR interfaces

Every invitation becomes a care episode: **Invited → Booked → Attended → Result in → Referred → Diagnostic done → Closed**
(normal results skip referral). Each stage has a target in days (`app/loop.py`, placeholders for the care-coordination team).

- **Inbound EHR events** move an episode one legal step forward and never close it:
  FHIR R4 `POST /api/fhir/Bundle` (Appointment, Encounter, Observation, ServiceRequest, DiagnosticReport; one
  OperationOutcome issue per entry) and HL7 v2 `POST /api/hl7` (ORU^R01 lab results, answered with an ACK, AA or AE).
  Both need `Authorization: Bearer $WIQAYA_INGEST_TOKEN`; with no token configured, ingest is off (503).
  Messages name their episode with FHIR `basedOn` or HL7 OBR-2 (placer order number).
- **Outbound:** `GET /api/fhir/Patient/{id}/$everything` returns a consent-filtered FHIR Bundle with a Task per episode.
- **Human oversight:** only a named clinician closes an episode (`POST /api/episodes/{id}/close`). An abnormal result
  closes only after a diagnostic report, or as a documented patient decline. Coordinators approve or dismiss each
  suggested outreach step; nothing is sent automatically.
- **Continuous monitor:** after every inbound result and on a timer (`WIQAYA_MONITOR_MINUTES`, default 30) the monitor
  re-scans the loop and raises alerts (new abnormal result, abnormal result past target, biggest delay moved), and
  refreshes the AI bottleneck brief.
- **Patient-reported barriers:** a coordinator or clinician records what the patient says is in the way (transport,
  time off work, interpreter, childcare, worry about the test, hard to reach, prefers a female clinician); the next
  step follows it.
- **Bottleneck analyst:** a Gemini agent (`/api/agent/bottlenecks`, or ask "where are patients stalling?") reads the loop
  summary and worklist, and explains the worst stage, open abnormal results, the barrier most linked to delay and the
  first cases to act on. Barrier and centre rates are hidden for groups under 10.
- The past 8 weeks are simulated against the hidden truth, as if their FHIR/HL7 events had arrived.
  The **Receive a lab result** button sends a generated ORU^R01 through the real HL7 path.

## AI: multi-agent copilot

The agents run on **Gemini** (`gemini-3.1-flash-lite`) everywhere. **Ollama is for local runs only**:

| Situation | Model |
| --- | --- |
| `GEMINI_API_KEY` set (local or deployed) | Gemini |
| Local run without a key, or `WIQAYA_LLM=ollama` | Ollama (`WIQAYA_MODEL`, default `gemma3:1b`) |
| Deployed (`WIQAYA_ENV=production`) without a key | No model: agents answer from rules |
| `WIQAYA_LLM=off` | No model: agents answer from rules |

Set `WIQAYA_ENV=production` on every deployment so Ollama is never used there.

Key: put `GEMINI_API_KEY=...` in `wiqaya/.env` (git-ignored, read at startup). The key goes in a request header,
never in a URL or a log.

| Agent | Job | Guardrail |
| --- | --- | --- |
| Supervisor | Routes a question: population query, case brief or safeguards | Rules decide on clear signals; a case brief needs a real person id |
| Query planner | Turns plain language into typed filters (JSON output) | Every value must be in the schema's allowed list |
| Verifier | Checks each proposed filter against the question's own words | Unsupported filters are dropped and logged |
| Analyst tool | Runs `engine.query()` over the population | Cells under 10 people suppressed |
| Narrator / Case reviewer / Safeguards reporter | Return JSON: a one-line lead plus which verified sentences answer the question | Code writes every fact sentence; the model only frames and selects |
| Outreach checker | Confirms household messages are template-only and name no genomic finding | Deterministic |
| Safety & grounding | Checks the lead | Rejects digits, number words, names, places, claims (percent, completed, target…), diagnostic or alarmist words; one revision, then the rule-based answer |

Every step shows in the UI as an agent trace (agent, action, MODEL or CODE, milliseconds). The model never
writes patient-facing messages: those stay approved templates. Machine learning also runs underneath: the
calibrated risk model, the invitation-lift (uplift) model and the benefit ranking.

Runtime architecture diagram (interactive, every box links to its source): `docs/architecture/wiqaya-runtime.html`;
editable source: `docs/architecture/wiqaya-runtime.archify.json`.

## What the demo shows

| Tab | What you see |
| --- | --- |
| Care loop | Funnel from invitation to signed closure with overdue counts per stage; the Gemini bottleneck analyst; a live HL7 feed; the follow-up worklist with explained next steps and coordinator approval; delay by barrier and centre; an episode timeline with clinician sign-off |
| Ask Wiqaya | Ask in plain language; see the answer, the data the analyst tool returned and the full agent trace |
| Weekly plan | One centre's 150 slots filled with whole households, ranked by benefit per slot, with back-to-back times that skip the midday prayer window |
| Person | Care gaps (rules) kept separate from the risk score (model), an 80% interval, top factors, invitation lift, the data used or withheld, the household messages, and accept/change/decline for the clinician |
| Safeguards | Reach, high-risk reach and calibration by nationality, region, sex, language, age band, or region × nationality; a reach gate; cells under 10 suppressed |
| Outcomes | Benefit ranking vs a calendar rule at the same capacity; the never-screened bias check; calibration by decile |

## How it works

| Module | Job |
| --- | --- |
| `app/data.py` | Synthetic population with a hidden ground truth, used only for evaluation |
| `app/rules.py` | Deterministic care-gap rules: Well-Child Visit, developmental, colorectal (earlier start with family history), cervical, breast, HbA1c, genetic counselling |
| `app/model.py` | Numpy logistic risk model trained **only on screened people** (bias correction), bootstrap intervals, exact additive explanations; T-learner invitation-lift model from past randomised outreach |
| `app/engine.py` | Priority = need × invitation lift, among people with a care gap; the weekly household plan; the calendar comparison policy; outcome scoring |
| `app/outreach.py` | Approved templates in Arabic and English; private messages unless the adult opted in to family messages; adolescents 14+ with their own contact get a confidential message; genomic findings never named |
| `app/loop.py` | Care episodes, stage targets, legal transitions, clinician closure rules, barriers, outreach strategies, funnel summary and worklist |
| `app/interop.py` | FHIR R4 bundle ingest and export, HL7 v2 ORU^R01 parsing and ACKs |
| `app/safeguards.py` | Segment table, reach gate, small-cell suppression with a complementary cell |
| `app/agents.py` | Multi-agent copilot: supervisor, query planner, verifier, analyst tool, writers, outreach checker, bottleneck analyst, safety and grounding |
| `app/llm.py` | Gemini client (Ollama on local runs only) with timeouts; returns None so callers fall back |
| `app/main.py` | FastAPI endpoints (`/api/agent/ask`, `/api/agent/brief/{id}` included) and the static UI |

## Results on synthetic data (seed 7, 8 centres × 150 slots, one week)

| Metric | Wiqaya | Calendar rule |
| --- | --- | --- |
| Expected completed visits | 757 | 677 |
| Completions caused by the invitation | 293 | 289 |
| Early findings caused by the invitation | 64.2 | 56.6 |

Across five seeds (7, 11, 23, 31, 47), Wiqaya always caused more early findings (+11% to +18%).
Completions caused by the invitation are roughly level with the calendar rule (+1% to +4%).
Bias check: for adults never screened, true risk is 7.8%; a naive model predicts 3.7%, the corrected model 7.1%.

## Assumptions and limitations

- Screening intervals and start ages are **placeholders**, not DoH-approved schedules.
- The adolescent confidentiality age (14) is a placeholder for local policy.
- Outreach is in Arabic and English only; the Arabic templates are drafts that need native-speaker and clinical review.
- Children are prioritised by rules only; there is no paediatric risk model yet.
- Ground-truth metrics (true risk, caused completions) exist only because the data is simulated.
  A real pilot measures them with a stepped-wedge rollout.
- Ranking by invitation lift deprioritises people unlikely to respond. The safeguards tab shows
  which segments fall below the reach gate; those need an outreach redesign, not silence.
- The demo sends synthetic data to the Gemini API. Before real patient data goes to Gemini, the pilot needs DoH approval,
  a data-processing agreement and in-region processing.
- For local Ollama runs, `gemma3:1b` is too small for the planner and writer roles; use `gemma3:4b` or larger.
- AI endpoints are limited to 20 requests a minute per client (`WIQAYA_ASK_LIMIT`), per process.
- End-to-end Selenium suite (90 tests) lives in the separate `wiqaya-qa` repository.
- Changes persist in one SQLite file. On Cloud Run that file lives on the instance's temporary disk, so it resets when the
  instance is replaced; a pilot uses a managed PostgreSQL in the UAE with encryption at rest, and keeps episodes as FHIR Tasks.
- Demo accounts and guest access exist for judging. A pilot replaces them with DoH single sign-on (UAE PASS / facility
  identity provider), multi-factor authentication and per-facility role assignment.
- EHR ingest uses one shared bearer token; a pilot adds mutual TLS from the interface engine and Malaffi onboarding.
- Pathway codes use a local code system; they must be mapped to DoH-approved LOINC and SNOMED CT sets.
