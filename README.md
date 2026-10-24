# Wiqaya: predictive prevention pathways (prototype)

Future Health Digital Innovation Challenge, Population Health. Wiqaya finds children, adults and
households with care gaps or elevated risk, and fills each primary-care centre's real weekly slots
with the households where an invitation does the most good. Every flag shows the consented data
behind it.

**All data is synthetic** (about 41,700 people in 12,000 households). No real records are used.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8099     # open http://localhost:8099
echo 'GEMINI_API_KEY=your-key' > .env          # without a key, a local run uses Ollama
.venv/bin/python -m pytest -q                  # 29 tests, no model or key needed
```

`WIQAYA_HOUSEHOLDS` sets the population size (default 12000; startup takes about 5 s).

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

## What the demo shows

| Tab | What you see |
| --- | --- |
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
| `app/safeguards.py` | Segment table, reach gate, small-cell suppression with a complementary cell |
| `app/agents.py` | Multi-agent copilot: supervisor, query planner, verifier, analyst tool, writers, outreach checker, safety and grounding |
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
- End-to-end Selenium suite (72 tests) lives in the separate `wiqaya-qa` repository.
- Clinician decisions are kept in memory (lost on restart). No authentication: demo only.
