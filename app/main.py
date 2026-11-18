"""Wiqaya prototype API. Synthetic data only; serves the single-page UI from /static."""
import hmac
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agents, interop, llm, loop, safeguards
from .engine import Engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = Engine(int(os.getenv("WIQAYA_HOUSEHOLDS", "12000")))
    yield


app = FastAPI(title="Wiqaya prototype", lifespan=lifespan)


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """The UI files change often during the demo; make browsers revalidate instead of serving a stale copy."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def engine(request: Request) -> Engine:
    return request.app.state.engine


def _person_or_404(e: Engine, pid: int):
    if not 0 <= pid < len(e.people):
        raise HTTPException(404, "Person not found")
    return e.people[pid]


# shortcut: per-process memory; use a shared store (e.g. Redis) if the app runs on several workers.
ASK_LIMIT, ASK_WINDOW_S = int(os.getenv("WIQAYA_ASK_LIMIT", "20")), 60.0
_asks = defaultdict(deque)


def ai_rate_limit(request: Request):
    """Caps model calls per client so a public demo can't run up the LLM bill."""
    now, q = time.monotonic(), _asks[request.client.host if request.client else "?"]
    while q and now - q[0] > ASK_WINDOW_S:
        q.popleft()
    if len(q) >= ASK_LIMIT:
        raise HTTPException(429, "Too many AI requests; try again in a minute")
    q.append(now)


def ingest_auth(request: Request):
    """EHR interface engines push with a shared bearer token. No token configured means ingest is off."""
    token = os.getenv("WIQAYA_INGEST_TOKEN")
    if not token:
        raise HTTPException(503, "EHR ingest is not configured on this server")
    sent = request.headers.get("authorization", "")
    if not hmac.compare_digest(sent.encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "Invalid or missing bearer token")


def _episode_or_404(e: Engine, eid: str):
    if eid not in e.episode:
        raise HTTPException(404, "Episode not found")
    return e.episode[eid]


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=400)


class Decision(BaseModel):
    person_id: int
    pathway: str = Field(min_length=1, max_length=120)
    action: Literal["accept", "change", "decline"]
    note: str = Field(default="", max_length=500)


class Closure(BaseModel):
    resolution: Literal[tuple(loop.RESOLUTIONS)]
    clinician: str = Field(min_length=3, max_length=80)
    note: str = Field(default="", max_length=500)


class OutreachDecision(BaseModel):
    coordinator: str = Field(min_length=3, max_length=80)
    decision: Literal["approve", "dismiss"]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/overview")
def overview(e: Engine = Depends(engine)):
    return e.overview()


@app.get("/api/plan/{centre}")
def plan(centre: str, e: Engine = Depends(engine)):
    if centre not in e.plans:
        raise HTTPException(404, "Unknown centre")
    return e.plan_view(centre)


@app.get("/api/person/{pid}")
def person(pid: int, e: Engine = Depends(engine)):
    _person_or_404(e, pid)
    return e.person_view(pid)


@app.post("/api/decisions", status_code=201)
def decide(d: Decision, e: Engine = Depends(engine)):
    _person_or_404(e, d.person_id)
    if d.pathway not in {g["pathway"] for g in e.gaps[d.person_id]}:
        raise HTTPException(422, "Pathway is not recommended for this person")
    record = d.model_dump() | {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    e.decisions.append(record)
    return record


@app.get("/api/safeguards")
def segment_checks(dimension: str = "nationality", e: Engine = Depends(engine)):
    if dimension not in safeguards.DIMENSIONS:
        raise HTTPException(422, f"dimension must be one of {list(safeguards.DIMENSIONS)}")
    return safeguards.segment_table(e, dimension)


@app.get("/api/agent/status")
def agent_status():
    return llm.status()


@app.post("/api/agent/ask", dependencies=[Depends(ai_rate_limit)])
def agent_ask(q: Question, e: Engine = Depends(engine)):
    return agents.ask(e, q.question)


@app.get("/api/agent/brief/{pid}", dependencies=[Depends(ai_rate_limit)])
def agent_brief(pid: int, e: Engine = Depends(engine)):
    _person_or_404(e, pid)
    return agents.brief(e, pid)


@app.get("/api/loop")
def loop_summary(e: Engine = Depends(engine)):
    return loop.summary(e)


@app.get("/api/loop/worklist")
def loop_worklist(stage: str | None = None, e: Engine = Depends(engine)):
    if stage is not None and stage not in loop.SLA:
        raise HTTPException(422, f"stage must be one of {list(loop.SLA)}")
    return loop.worklist(e, stage)


@app.get("/api/episodes/{eid}")
def episode(eid: str, e: Engine = Depends(engine)):
    ep = _episode_or_404(e, eid)
    return loop.view(ep, e.people[ep["person_id"]])


@app.post("/api/episodes/{eid}/close")
def close_episode(eid: str, c: Closure, e: Engine = Depends(engine)):
    """Clinician sign-off: the only way an episode closes."""
    ep = _episode_or_404(e, eid)
    try:
        loop.close(ep, c.resolution, c.clinician, c.note)
    except loop.LoopError as err:
        raise HTTPException(409, str(err)) from err
    return loop.view(ep, e.people[ep["person_id"]])


@app.post("/api/episodes/{eid}/outreach")
def outreach_decision(eid: str, d: OutreachDecision, e: Engine = Depends(engine)):
    """A coordinator approves or dismisses the suggested strategy; nothing is sent automatically."""
    ep = _episode_or_404(e, eid)
    p = e.people[ep["person_id"]]
    if loop.stage(ep) == "closed":
        raise HTTPException(409, "Episode is already closed")
    ep.setdefault("approvals", []).append(dict(on=str(loop.TODAY), by=d.coordinator, decision=d.decision,
                                               action=loop.strategy(ep, p)["action"]))
    return loop.view(ep, p)


@app.get("/api/agent/bottlenecks", dependencies=[Depends(ai_rate_limit)])
def agent_bottlenecks(e: Engine = Depends(engine)):
    return agents.bottlenecks(e)


@app.post("/api/fhir/Bundle", dependencies=[Depends(ingest_auth)])
def fhir_bundle(bundle: dict = Body(...), e: Engine = Depends(engine)):
    try:
        return interop.ingest_bundle(e, bundle)
    except interop.InteropError as err:
        raise HTTPException(422, str(err)) from err


@app.post("/api/hl7", dependencies=[Depends(ingest_auth)], response_class=PlainTextResponse)
async def hl7_message(request: Request, e: Engine = Depends(engine)):
    body = await request.body()
    if len(body) > 20_000:
        raise HTTPException(413, "HL7 message too large")
    ack, _ = interop.ingest_hl7(e, body.decode("utf-8", "replace"))
    return ack


@app.get("/api/fhir/Patient/{pid}/$everything")
def fhir_everything(pid: int, e: Engine = Depends(engine)):
    _person_or_404(e, pid)
    return interop.patient_bundle(e, pid)


@app.post("/api/feed/demo", dependencies=[Depends(ai_rate_limit)])
def feed_demo(e: Engine = Depends(engine)):
    """Generates a lab result for an episode waiting on one and runs it through the real HL7 path."""
    waiting = [ep for ep in e.episodes if loop.stage(ep) == "attended"]
    if not waiting:
        raise HTTPException(409, "No episode is waiting for a result")
    n = len(e.feed_log)
    ep = waiting[n * 7919 % len(waiting)]
    abnormal = n % 3 == 0  # every third demo result is abnormal, so the loop has something to escalate
    msg = interop.sample_oru(e, ep, abnormal, f"DEMO{n + 1:04d}")
    ack, detail = interop.ingest_hl7(e, msg)
    e.feed_log.append(dict(control=f"DEMO{n + 1:04d}", episode=ep["id"], detail=detail))
    return dict(message=msg, ack=ack, episode=loop.view(ep, e.people[ep["person_id"]]))


@app.get("/api/outcomes")
def outcomes(e: Engine = Depends(engine)):
    return e.outcomes()


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True), name="static")
