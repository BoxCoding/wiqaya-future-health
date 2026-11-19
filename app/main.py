"""Wiqaya prototype API. Synthetic data only; serves the single-page UI from /static.

Every /api route except /health and /api/auth/* needs a signed-in user (app/auth.py), and every access to
an individual record or change to one is written to the hash-chained audit log (app/store.py).
EHR interface engines push FHIR/HL7 with a bearer token instead of a user session.
"""
import asyncio
import hmac
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agents, auth, interop, llm, loop, safeguards
from .engine import Engine
from .store import Store, now

RECORDS = ("clinician", "coordinator")  # roles that may see named individuals
# Endpoints that change care data are `async def` with no awaits inside, so they run one at a time on the event
# loop, like the monitor: two clinicians closing the same episode at once cannot both pass the stage check.
# shortcut: holds for one process (max-instances 1); several instances need a database transaction per change.
MONITOR_S = float(os.getenv("WIQAYA_MONITOR_MINUTES", "30")) * 60


async def monitor(app):
    """Continuous bottleneck monitor: re-scan the loop and refresh the AI brief on a timer."""
    while True:
        e = app.state.engine
        loop.scan(e, now())
        with suppress(Exception):  # a model outage must never stop monitoring; the brief falls back to rules anyway
            e.auto_brief = (await asyncio.to_thread(agents.bottlenecks, e)) | {"at": now()}
        await asyncio.sleep(MONITOR_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    e = Engine(int(os.getenv("WIQAYA_HOUSEHOLDS", "12000")))
    store = Store(os.getenv("WIQAYA_DB", "wiqaya.db"))
    auth.seed(store)
    for snap in store.episodes():  # replay every change saved before the last restart
        if snap["id"] in e.episode:
            e.episode[snap["id"]].clear()
            e.episode[snap["id"]].update(snap)
    e.decisions = store.decisions()
    app.state.engine, app.state.store = e, store
    task = asyncio.create_task(monitor(app))
    yield
    task.cancel()


app = FastAPI(title="Wiqaya prototype", lifespan=lifespan)


@app.middleware("http")
async def headers(request: Request, call_next):
    """No caching of UI files during the demo, and no caching or framing of anything."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def engine(request: Request) -> Engine:
    return request.app.state.engine


def store(request: Request) -> Store:
    return request.app.state.store


def audit(request: Request, s, action, target="", detail=""):
    request.app.state.store.audit(s["u"], s["role"], action, target, detail, auth.client_ip(request))


def changed(request: Request, ids):
    """Persist changed episodes and let the monitor react straight away."""
    e = request.app.state.engine
    for i in dict.fromkeys(i for i in ids if i):
        request.app.state.store.save_episode(e.episode[i])
    loop.scan(e, now())


def _person_or_404(e: Engine, pid: int):
    if not 0 <= pid < len(e.people):
        raise HTTPException(404, "Person not found")
    return e.people[pid]


def _episode_or_404(e: Engine, eid: str):
    if eid not in e.episode:
        raise HTTPException(404, "Episode not found")
    return e.episode[eid]


# shortcut: per-process memory; use a shared store (e.g. Redis) if the app runs on several workers.
ASK_LIMIT, ASK_WINDOW_S = int(os.getenv("WIQAYA_ASK_LIMIT", "20")), 60.0
LOGIN_LIMIT, LOGIN_WINDOW_S = 10, 300.0
_asks, _logins = defaultdict(deque), defaultdict(deque)


def _limit(bucket, key, limit, window, msg, count=True):
    now_, q = time.monotonic(), bucket[key]
    while q and now_ - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, msg)
    if count:
        q.append(now_)


def ai_rate_limit(request: Request):
    """Caps model calls per client so a public demo can't run up the LLM bill."""
    _limit(_asks, auth.client_ip(request), ASK_LIMIT, ASK_WINDOW_S, "Too many AI requests; try again in a minute")


def ingest_auth(request: Request):
    """EHR interface engines push with a shared bearer token. No token configured means ingest is off."""
    token = os.getenv("WIQAYA_INGEST_TOKEN")
    if not token:
        raise HTTPException(503, "EHR ingest is not configured on this server")
    sent = request.headers.get("authorization", "")
    if not hmac.compare_digest(sent.encode(), f"Bearer {token}".encode()):
        request.app.state.store.audit("interface-engine", "system", "ingest.denied", request.url.path, "bad or missing token",
                                      auth.client_ip(request))
        raise HTTPException(401, "Invalid or missing bearer token")
    return dict(u="interface-engine", role="system")


ANY = Depends(auth.current)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class Guest(BaseModel):
    role: Literal[tuple(auth.ROLES)]


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=400)


class Decision(BaseModel):
    person_id: int
    pathway: str = Field(min_length=1, max_length=120)
    action: Literal["accept", "change", "decline"]
    note: str = Field(default="", max_length=500)


class Closure(BaseModel):
    resolution: Literal[tuple(loop.RESOLUTIONS)]
    note: str = Field(default="", max_length=500)


class OutreachDecision(BaseModel):
    decision: Literal["approve", "dismiss"]


class Barrier(BaseModel):
    code: Literal[tuple(loop.REPORTED)]
    note: str = Field(default="", max_length=300)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- sign-in ----------
def _start(response: Response, s):
    response.set_cookie(auth.COOKIE, auth.sign(s), max_age=auth.TTL_S, httponly=True, samesite="strict",
                        secure=not llm.is_local(), path="/")
    return auth.public(s)


@app.get("/api/auth/options")
def auth_options():
    """What the login page offers. Demo accounts and the shared demo password are shown only in a demo."""
    demo = os.getenv("WIQAYA_DEMO_USERS", "1") != "0"
    return dict(guest=auth.guest_enabled(), roles=auth.ROLES,
                demo_users=[dict(username=u, name=n, role=r) for u, n, r in auth.DEMO_USERS] if demo else [],
                demo_password=auth.DEMO_PASSWORD if demo else None)


@app.post("/api/auth/login")
def login(body: Login, request: Request, response: Response, st: Store = Depends(store)):
    ip = auth.client_ip(request)
    msg = "Too many failed sign-ins; wait five minutes"
    _limit(_logins, ip, LOGIN_LIMIT, LOGIN_WINDOW_S, msg, count=False)  # only failures count: a shared venue Wi-Fi is fine
    u = st.user(body.username.strip().lower())
    if not u or not auth.check_password(body.password, u["pw"]):
        _limit(_logins, ip, LOGIN_LIMIT, LOGIN_WINDOW_S, msg)
        st.audit(body.username[:60], "-", "login.failure", "", "", ip)
        raise HTTPException(401, "Wrong username or password")
    s = auth.session_for(u["username"], u["name"], u["role"])
    st.audit(s["u"], s["role"], "login.success", "", "", ip)
    return _start(response, s)


@app.post("/api/auth/guest")
def guest(body: Guest, request: Request, response: Response, st: Store = Depends(store)):
    if not auth.guest_enabled():
        raise HTTPException(403, "Guest access is turned off")
    s = auth.session_for(f"guest-{body.role}", f"Guest {auth.ROLES[body.role].lower()}", body.role, guest=True)
    st.audit(s["u"], s["role"], "login.guest", "", "", auth.client_ip(request))
    return _start(response, s)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    s = auth.unsign(request.cookies.get(auth.COOKIE))
    if s:
        audit(request, s, "logout")
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    """The signed-in user, or null (not 401: the login page asks this before anyone has signed in)."""
    s = auth.unsign(request.cookies.get(auth.COOKIE))
    if not s:
        return None
    return auth.public(s)


# ---------- population views (every role) ----------
@app.get("/api/overview", dependencies=[ANY])
def overview(e: Engine = Depends(engine)):
    return e.overview()


@app.get("/api/safeguards", dependencies=[ANY])
def segment_checks(dimension: str = "nationality", e: Engine = Depends(engine)):
    if dimension not in safeguards.DIMENSIONS:
        raise HTTPException(422, f"dimension must be one of {list(safeguards.DIMENSIONS)}")
    return safeguards.segment_table(e, dimension)


@app.get("/api/outcomes", dependencies=[ANY])
def outcomes(e: Engine = Depends(engine)):
    return e.outcomes()


@app.get("/api/loop", dependencies=[ANY])
def loop_summary(e: Engine = Depends(engine)):
    return loop.summary(e)


@app.get("/api/loop/alerts")
def loop_alerts(s=ANY, e: Engine = Depends(engine)):
    brief = e.auto_brief
    if brief and s["role"] not in RECORDS:
        brief = brief | {"facts": {k: v for k, v in brief["facts"].items() if k != "worklist"}}
    return dict(alerts=e.alerts, last_scan=e.monitor.get("last_scan"), every_minutes=MONITOR_S / 60, auto_brief=brief)


@app.get("/api/agent/status", dependencies=[ANY])
def agent_status():
    return llm.status()


@app.post("/api/agent/ask", dependencies=[Depends(ai_rate_limit)])
def agent_ask(q: Question, request: Request, s=ANY, e: Engine = Depends(engine)):
    audit(request, s, "ai.ask", "", q.question)
    if s["role"] not in RECORDS and agents.parse_rules(q.question)[1] is not None:
        raise HTTPException(403, f"Your role ({auth.ROLES[s['role']]}) sees population figures, not individual records")
    r = agents.ask(e, q.question)
    if s["role"] not in RECORDS and r["facts"]:  # population roles never see names
        f = r["facts"]
        r["facts"] = f | {"result": f["result"] | {"top": []}} if "result" in f else {k: v for k, v in f.items() if k != "worklist"}
    return r


@app.get("/api/agent/bottlenecks", dependencies=[Depends(ai_rate_limit)])
def agent_bottlenecks(request: Request, s=ANY, e: Engine = Depends(engine)):
    audit(request, s, "ai.bottlenecks")
    r = agents.bottlenecks(e)
    if s["role"] not in RECORDS:
        r["facts"] = {k: v for k, v in r["facts"].items() if k != "worklist"}
    return r


# ---------- individual records (clinicians and coordinators) ----------
@app.get("/api/plan/{centre}")
def plan(centre: str, request: Request, s=Depends(auth.require(*RECORDS)), e: Engine = Depends(engine)):
    if centre not in e.plans:
        raise HTTPException(404, "Unknown centre")
    audit(request, s, "plan.view", centre)
    return e.plan_view(centre)


@app.get("/api/person/{pid}")
def person(pid: int, request: Request, s=Depends(auth.require(*RECORDS)), e: Engine = Depends(engine)):
    _person_or_404(e, pid)
    audit(request, s, "person.view", pid)
    return e.person_view(pid)


@app.get("/api/agent/brief/{pid}", dependencies=[Depends(ai_rate_limit)])
def agent_brief(pid: int, request: Request, s=Depends(auth.require("clinician")), e: Engine = Depends(engine)):
    _person_or_404(e, pid)
    audit(request, s, "ai.brief", pid)
    return agents.brief(e, pid)


@app.post("/api/decisions", status_code=201)
async def decide(d: Decision, request: Request, s=Depends(auth.require("clinician")), e: Engine = Depends(engine)):
    _person_or_404(e, d.person_id)
    if d.pathway not in {g["pathway"] for g in e.gaps[d.person_id]}:
        raise HTTPException(422, "Pathway is not recommended for this person")
    record = d.model_dump() | {"at": now(), "by": auth.display(s)}
    e.decisions.append(record)
    request.app.state.store.save_decision(record)
    audit(request, s, f"decision.{d.action}", d.person_id, d.pathway)
    return record


@app.get("/api/loop/worklist")
def loop_worklist(request: Request, stage: str | None = None, s=Depends(auth.require(*RECORDS)), e: Engine = Depends(engine)):
    if stage is not None and stage not in loop.SLA:
        raise HTTPException(422, f"stage must be one of {list(loop.SLA)}")
    audit(request, s, "worklist.view", stage or "all")
    return loop.worklist(e, stage)


@app.get("/api/episodes/{eid}")
def episode(eid: str, request: Request, s=Depends(auth.require(*RECORDS)), e: Engine = Depends(engine)):
    ep = _episode_or_404(e, eid)
    audit(request, s, "episode.view", eid)
    return loop.view(ep, e.people[ep["person_id"]])


@app.post("/api/episodes/{eid}/close")
async def close_episode(eid: str, c: Closure, request: Request, s=Depends(auth.require("clinician")), e: Engine = Depends(engine)):
    """Clinician sign-off: the only way an episode closes. The signer is the signed-in clinician."""
    ep = _episode_or_404(e, eid)
    try:
        loop.close(ep, c.resolution, auth.display(s), c.note)
    except loop.LoopError as err:
        raise HTTPException(409, str(err)) from err
    audit(request, s, "episode.close", eid, c.resolution)
    changed(request, [eid])
    return loop.view(ep, e.people[ep["person_id"]])


@app.post("/api/episodes/{eid}/outreach")
async def outreach_decision(eid: str, d: OutreachDecision, request: Request, s=Depends(auth.require("coordinator")),
                      e: Engine = Depends(engine)):
    """A coordinator approves or dismisses the suggested strategy; nothing is sent automatically."""
    ep = _episode_or_404(e, eid)
    p = e.people[ep["person_id"]]
    if loop.stage(ep) == "closed":
        raise HTTPException(409, "Episode is already closed")
    action = loop.strategy(ep, p)["action"]
    ep.setdefault("approvals", []).append(dict(on=str(loop.TODAY), by=auth.display(s), decision=d.decision, action=action))
    audit(request, s, f"outreach.{d.decision}", eid, action)
    changed(request, [eid])
    return loop.view(ep, p)


@app.post("/api/episodes/{eid}/barriers")
async def report_barrier(eid: str, b: Barrier, request: Request, s=Depends(auth.require(*RECORDS)), e: Engine = Depends(engine)):
    """Records what the patient says is in the way; the suggested next step follows it."""
    ep = _episode_or_404(e, eid)
    try:
        loop.record_barrier(ep, b.code, b.note, auth.display(s))
    except loop.LoopError as err:
        raise HTTPException(409, str(err)) from err
    audit(request, s, "barrier.record", eid, b.code)
    changed(request, [eid])
    return loop.view(ep, e.people[ep["person_id"]])


@app.get("/api/fhir/Patient/{pid}/$everything")
def fhir_everything(pid: int, request: Request, s=Depends(auth.require(*RECORDS)), e: Engine = Depends(engine)):
    _person_or_404(e, pid)
    audit(request, s, "fhir.export", pid)
    return interop.patient_bundle(e, pid)


@app.post("/api/feed/demo", dependencies=[Depends(ai_rate_limit)])
async def feed_demo(request: Request, s=Depends(auth.require(*RECORDS)), e: Engine = Depends(engine)):
    """Generates a lab result for an episode waiting on one and runs it through the real HL7 path."""
    waiting = [ep for ep in e.episodes if loop.stage(ep) == "attended"]
    if not waiting:
        raise HTTPException(409, "No episode is waiting for a result")
    n = len(e.feed_log)
    ep = waiting[n * 7919 % len(waiting)]
    abnormal = n % 3 == 0  # every third demo result is abnormal, so the loop has something to escalate
    msg = interop.sample_oru(e, ep, abnormal, f"DEMO{n + 1:04d}")
    ack, eid = interop.ingest_hl7(e, msg)
    e.feed_log.append(dict(control=f"DEMO{n + 1:04d}", episode=ep["id"], ok=eid is not None))
    audit(request, s, "feed.demo", ep["id"], "abnormal" if abnormal else "normal")
    changed(request, [eid])
    return dict(message=msg, ack=ack, episode=loop.view(ep, e.people[ep["person_id"]]))


# ---------- audit (privacy officer) ----------
@app.get("/api/audit")
def audit_log(request: Request, action: str | None = None, limit: int = 200, s=Depends(auth.require("admin")),
              st: Store = Depends(store)):
    audit(request, s, "audit.view", action or "all")
    return dict(rows=st.audits(min(max(limit, 1), 500), action), chain_ok=st.verify(), users=st.users())


# ---------- EHR interfaces (system to system) ----------
@app.post("/api/fhir/Bundle")
async def fhir_bundle(request: Request, bundle: dict = Body(...), sysu=Depends(ingest_auth), e: Engine = Depends(engine)):
    try:
        outcome, ids = interop.ingest_bundle(e, bundle)
    except interop.InteropError as err:
        raise HTTPException(422, str(err)) from err
    audit(request, sysu, "ingest.fhir", ",".join(ids), f"{len(outcome['issue'])} entries")
    changed(request, ids)
    return outcome


@app.post("/api/hl7", response_class=PlainTextResponse)
async def hl7_message(request: Request, sysu=Depends(ingest_auth), e: Engine = Depends(engine)):
    body = await request.body()
    if len(body) > 20_000:
        raise HTTPException(413, "HL7 message too large")
    ack, eid = interop.ingest_hl7(e, body.decode("utf-8", "replace"))
    audit(request, sysu, "ingest.hl7", eid or "", "AA" if eid else "AE")
    changed(request, [eid])
    return ack


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True), name="static")
