"""Wiqaya prototype API. Synthetic data only; serves the single-page UI from /static."""
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agents, llm, safeguards
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


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=400)


class Decision(BaseModel):
    person_id: int
    pathway: str = Field(min_length=1, max_length=120)
    action: Literal["accept", "change", "decline"]
    note: str = Field(default="", max_length=500)


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


@app.get("/api/outcomes")
def outcomes(e: Engine = Depends(engine)):
    return e.outcomes()


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True), name="static")
