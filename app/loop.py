"""Screening-to-care loop: every invitation is tracked from invite to a clinician-verified closure.

An episode moves forward through STAGES. Booking, attendance, results, referrals and diagnostic reports
arrive as FHIR resources or HL7 v2 results (app/interop.py); only a clinician can close an episode, and
an abnormal result can only close after a diagnostic report, or with a documented patient decline.

shortcut: SLAs, barriers and outreach strategies are placeholders for the care-coordination team to set.
"""
from datetime import date, timedelta
from statistics import median

import numpy as np

from .outreach import WEEK_START
from .safeguards import MIN_CELL

STAGES = ["invited", "booked", "attended", "resulted", "referred", "diagnosed", "closed"]
LABEL = {"invited": "Invited", "booked": "Booked", "attended": "Attended", "resulted": "Result in",
         "referred": "Referred", "diagnosed": "Diagnostic done", "closed": "Closed"}
# Days an episode may wait in a stage before it is overdue.
SLA = {"invited": 14, "booked": 21, "attended": 7, "resulted": 7, "referred": 30, "diagnosed": 7}
WAITING_FOR = {"invited": "booking", "booked": "visit", "attended": "result", "resulted": "referral or sign-off",
               "referred": "diagnostic test", "diagnosed": "clinician sign-off"}
SOURCE = {"invited": "Wiqaya plan", "booked": "FHIR Appointment", "attended": "FHIR Encounter", "resulted": "HL7 ORU^R01",
          "referred": "FHIR ServiceRequest", "diagnosed": "FHIR DiagnosticReport", "closed": "Clinician sign-off"}
RESOLUTIONS = {
    "normal-recall": "Normal result: routine recall scheduled",
    "diagnostic-negative": "Diagnostic test negative: return to routine screening",
    "diagnosis-treatment": "Diagnosis confirmed: treatment started",
    "specialist-referral": "Diagnosis confirmed: handed to specialist care",
    "patient-declined": "Patient declined further tests (documented)",
}
# Barriers a patient tells the coordinator about, and the outreach step each one calls for.
REPORTED = {
    "transport": ("No transport to the clinic", "Arrange transport, or book the nearest centre"),
    "work": ("Cannot take time off work", "Offer an evening or Saturday slot"),
    "language": ("Needs an interpreter", "Call with an interpreter in the preferred language"),
    "childcare": ("No childcare", "Book a family slot so the children are seen in the same visit"),
    "fear": ("Worried about the test", "Nurse call to explain the test and what happens next"),
    "unreachable": ("Hard to reach by phone", "Try the alternate contact, or a community health worker visit"),
    "female": ("Prefers a female clinician", "Book the visit with a female clinician"),
}
TODAY = WEEK_START
CLINICIANS = ["Dr. Al Mansoori", "Dr. Haddad", "Dr. Rao", "Dr. Santos", "Dr. Al Ketbi", "Dr. Nasser"]


class LoopError(ValueError):
    """A transition the loop does not allow; the API returns it as 409."""


def _day(d):
    return d.isoformat()


def _event(stage, on, detail, by=None, source=None):
    return dict(stage=stage, on=_day(on), source=source or SOURCE[stage], detail=detail, by=by)


def barriers(p):
    """Observed, consented facts that make follow-up harder; each one explains a strategy."""
    out = []
    if p["consent"]["context"] and p["distance_km"] > 15:
        out.append("Lives far from the clinic")
    if p["past_attendance"] < 0.4:
        out.append("Often misses visits")
    if p["age"] >= 65:
        out.append("Aged 65+")
    if p["age"] < 18 and not p["own_contact"]:
        out.append("Reached through a guardian")
    return out


def reported(ep):
    return [REPORTED[r["code"]][0] for r in ep.get("reported", [])]


def record_barrier(ep, code, note, by, on=None):
    if code not in REPORTED:
        raise LoopError(f"Unknown barrier '{code}'")
    if stage(ep) == "closed":
        raise LoopError("Episode is already closed")
    ep.setdefault("reported", []).append(dict(code=code, label=REPORTED[code][0], note=note.strip(), by=by, on=_day(on or TODAY)))


def stage(ep):
    return ep["events"][-1]["stage"]


def last_on(ep):
    return date.fromisoformat(ep["events"][-1]["on"])


def status(ep, today=TODAY):
    """Where the episode stands: days waiting, SLA, overdue and what it is waiting for."""
    s = stage(ep)
    waited = (today - last_on(ep)).days
    if s == "closed":
        return dict(stage=s, waiting_for=None, days=waited, sla=None, overdue=0)
    sla = SLA[s]
    if s == "booked" and ep.get("appointment") and date.fromisoformat(ep["appointment"]) >= today:
        waited = 0  # a future appointment is not a delay
    waiting = ("referral" if ep["abnormal"] else "clinician sign-off") if s == "resulted" else WAITING_FOR[s]
    return dict(stage=s, waiting_for=waiting, days=waited, sla=sla, overdue=max(waited - sla, 0))


def strategy(ep, p, today=TODAY):
    """The next outreach step for a stalled episode, with the reason. Rules only; a coordinator approves it."""
    st, bars = status(ep, today), barriers(p)
    s = st["stage"]
    told = ep.get("reported", [])
    if told and s in ("invited", "booked", "referred"):  # what the patient said beats what the data suggests
        label, action = REPORTED[told[-1]["code"]]
        return dict(action=action, why=f"Patient reported: {label.lower()}; {st['days']} days at '{LABEL[s]}'")
    if s in ("resulted",) and ep["abnormal"]:
        return dict(action="Clinician call within 48 hours to book the diagnostic test",
                    why=f"Abnormal result waiting {st['days']} days with no referral")
    if s == "referred":
        return dict(action="Coordinator books the diagnostic slot directly and confirms by phone",
                    why=f"Referred {st['days']} days ago; diagnostic test not done")
    if s in ("diagnosed", "resulted"):
        return dict(action="Send to the responsible clinician's sign-off queue",
                    why=f"Result ready {st['days']} days; loop not closed")
    if s == "booked":
        return dict(action="Same-week rebooking call", why="Missed the booked appointment")
    if s == "attended":
        return dict(action="Chase the lab for the result", why=f"Visit {st['days']} days ago; no result received")
    channel = ("Nurse phone call" if "Aged 65+" in bars or "Often misses visits" in bars
               else "Call the guardian; keep the confidential part of the visit" if "Reached through a guardian" in bars
               else f"Second reminder in {'Arabic' if p['language'] == 'ar' else 'English'}")
    extra = "; offer the nearest centre or a Saturday slot" if "Lives far from the clinic" in bars else ""
    return dict(action=channel + extra, why=f"No booking {st['days']} days after the invitation"
                + (f" ({', '.join(bars).lower()})" if bars else ""))


def urgency(ep, st):
    """Abnormal results first, then the longest overdue."""
    return (3 if ep["abnormal"] and st["stage"] in ("resulted", "referred", "diagnosed") else 1) * (st["overdue"] + 1)


# ---------- simulation of past weeks (synthetic EHR feed) ----------
def simulate(engine, weeks=8, per_week=600, seed=11):
    """Past invitations replayed against the hidden truth, as if their FHIR/HL7 events had arrived."""
    rng = np.random.default_rng(seed)
    due = [p for p in engine.people if p["id"] in engine.priority and p["consent"]["outreach"]]
    episodes = []

    def new(p, invited_on):
        g = engine.gaps[p["id"]][0]
        ep = dict(id=f"E{len(episodes) + 1:05d}", person_id=p["id"], pathway=g["pathway"], abnormal=None,
                  appointment=None, result=None, resolution=None, events=[_event("invited", invited_on, f"Invited for {g['pathway']}")])
        episodes.append(ep)
        return ep

    for w in range(weeks, 0, -1):
        inv = TODAY - timedelta(days=7 * w)
        for i in rng.choice(len(due), per_week, replace=False):
            p = due[i]
            t = p["_truth"]
            ep = new(p, inv)
            bars = barriers(p)
            slow = 1 + 0.6 * len(bars)
            if rng.random() > min(t["p1"] * 1.1, 0.95):
                continue  # never booked: stays invited
            on = inv + timedelta(days=int(1 + rng.exponential(4 * slow)))
            if on > TODAY:
                continue
            appt = on + timedelta(days=int(rng.integers(3, 15)))
            ep["appointment"] = _day(appt)
            ep["events"].append(_event("booked", on, f"Appointment {appt:%d %b} at {p['centre']}"))
            if appt > TODAY or rng.random() < 0.08 * slow:
                continue  # upcoming, or a no-show waiting for rebooking
            ep["events"].append(_event("attended", appt, "Screening visit completed"))
            res = appt + timedelta(days=int(rng.integers(1, 9 if rng.random() < 0.85 else 20)))
            if res > TODAY:
                continue
            abnormal = bool(rng.random() < min(0.06 + t["risk"] * 1.4, 0.6))
            ep["abnormal"] = abnormal
            ep["result"] = dict(flag="A" if abnormal else "N", text="Abnormal: further tests needed" if abnormal else "Normal")
            ep["events"].append(_event("resulted", res, ep["result"]["text"]))
            if not abnormal:
                done = res + timedelta(days=int(rng.integers(1, 12)))
                if done <= TODAY and rng.random() < 0.85:
                    close(ep, "normal-recall", str(rng.choice(CLINICIANS)), "", on=done)
                continue
            ref = res + timedelta(days=int(rng.exponential(6 * slow)))
            if ref > TODAY or rng.random() < 0.12:
                continue
            ep["events"].append(_event("referred", ref, "Referred for diagnostic test"))
            dx = ref + timedelta(days=int(rng.integers(7, 45)))
            if dx > TODAY or rng.random() < 0.1:
                continue
            confirmed = bool(rng.random() < 0.35)
            ep["events"].append(_event("diagnosed", dx, "Diagnosis confirmed" if confirmed else "Diagnostic test negative"))
            ep["confirmed"] = confirmed
            done = dx + timedelta(days=int(rng.integers(1, 10)))
            if done <= TODAY and rng.random() < 0.8:
                close(ep, "diagnosis-treatment" if confirmed else "diagnostic-negative", str(rng.choice(CLINICIANS)), "", on=done)
    for pid in sorted(engine.contacted):  # this week's plan: invited today
        new(engine.people[pid], TODAY)
    return episodes


# ---------- transitions (shared by the simulation, FHIR/HL7 ingest and the clinician UI) ----------
def advance(ep, to, on, detail, source=None, **fields):
    """Move an episode forward one legal step; anything else raises LoopError."""
    s = stage(ep)
    allowed = {"invited": {"booked"}, "booked": {"attended", "booked"}, "attended": {"resulted"},
               "resulted": {"referred"} if ep["abnormal"] else set(), "referred": {"diagnosed"}, "diagnosed": set(), "closed": set()}
    if to not in allowed[s]:
        raise LoopError(f"Episode {ep['id']} is '{s}'; cannot move to '{to}'")
    if not last_on(ep) <= on <= TODAY:
        raise LoopError(f"Event date {on} is before the episode's last event or in the future")
    ep.update(fields)
    ep["events"].append(_event(to, on, detail, source=source))


def closure_options(ep):
    """The resolutions a clinician may sign now. Abnormal results close only after a diagnostic report,
    or as a documented decline. The UI offers exactly these; close() accepts nothing else."""
    s = stage(ep)
    if s == "diagnosed":
        return ["diagnostic-negative", "diagnosis-treatment", "specialist-referral"]
    if s in ("resulted", "referred") and ep["abnormal"]:
        return ["patient-declined"]
    return ["normal-recall"] if s == "resulted" else []


def close(ep, code, clinician, note, on=TODAY):
    """Clinician sign-off."""
    if code not in closure_options(ep):
        raise LoopError(f"Episode {ep['id']} is '{LABEL[stage(ep)]}'; it can close only with: {', '.join(closure_options(ep)) or 'nothing yet'}")
    if not clinician.strip():
        raise LoopError("A named clinician must sign off")
    if code == "patient-declined" and len(note.strip()) < 10:
        raise LoopError("Document the decline in the note (at least 10 characters)")
    ep["resolution"] = dict(code=code, label=RESOLUTIONS[code], by=clinician.strip(), note=note.strip(), on=_day(on))
    ep["events"].append(_event("closed", on, RESOLUTIONS[code] + (f". {note.strip()}" if note.strip() else ""), by=clinician.strip()))


# ---------- views ----------
def view(ep, p):
    st = status(ep)
    return dict(id=ep["id"], person_id=p["id"], name=p["name"], age=p["age"], centre=p["centre"], pathway=ep["pathway"],
                abnormal=ep["abnormal"], result=ep["result"], resolution=ep["resolution"], events=ep["events"],
                status=st, stage_label=LABEL[st["stage"]], barriers=barriers(p), reported=ep.get("reported", []),
                strategy=None if st["stage"] == "closed" else strategy(ep, p),
                closable=[dict(code=c, label=RESOLUTIONS[c]) for c in closure_options(ep)], approvals=ep.get("approvals", []))


def summary(engine):
    """Funnel, delays and the worst bottleneck. Groups under MIN_CELL are suppressed."""
    eps = engine.episodes
    rows = [(ep, status(ep), engine.people[ep["person_id"]]) for ep in eps]
    by_stage = {s: [r for r in rows if r[1]["stage"] == s] for s in STAGES}
    stages = [dict(stage=s, label=LABEL[s], count=len(rs), overdue=sum(r[1]["overdue"] > 0 for r in rs),
                   median_days=None if not rs else round(median(r[1]["days"] for r in rs)), sla=SLA.get(s))
              for s, rs in by_stage.items()]
    open_rows = [r for r in rows if r[1]["stage"] != "closed"]
    overdue = [r for r in open_rows if r[1]["overdue"] > 0]
    abnormal_open = [r for r in open_rows if r[0]["abnormal"]]
    closed = by_stage["closed"]
    to_close = [(date.fromisoformat(ep["events"][-1]["on"]) - date.fromisoformat(ep["events"][0]["on"])).days
                for ep, _, _ in closed if ep["abnormal"]]
    def delay(key, name, group):
        """A group under MIN_CELL shows nothing: hiding only the count while showing a rate would give the count away."""
        late = sum(r[1]["overdue"] > 0 for r in group)
        small = len(group) < MIN_CELL
        return {key: name, "open": None if small else len(group), "overdue": None if small else late,
                "rate": None if small else round(late / len(group), 3)}

    tags = [(r, set(barriers(r[2])) | set(reported(r[0]))) for r in open_rows]
    barrier_rows = [delay("barrier", b, [r for r, t in tags if b in t])
                    for b in ["Lives far from the clinic", "Often misses visits", "Aged 65+", "Reached through a guardian",
                              *(label for label, _ in REPORTED.values())]]
    centres = [delay("centre", c, [r for r in open_rows if r[2]["centre"] == c]) for c in engine.centres]
    worst = max((s for s in stages if s["stage"] != "closed"), key=lambda s: s["overdue"])
    return dict(today=_day(TODAY), total=len(rows), open=len(open_rows), closed=len(closed), overdue=len(overdue),
                abnormal_open=len(abnormal_open), abnormal_overdue=sum(r[1]["overdue"] > 0 for r in abnormal_open),
                awaiting_signoff=sum(1 for r in open_rows if r[1]["waiting_for"] == "clinician sign-off"),
                median_days_to_close_abnormal=round(median(to_close)) if to_close else None,
                stages=stages, worst=worst, barriers=barrier_rows, centres=centres, sla=SLA,
                reported_options=[dict(code=c, label=label) for c, (label, _) in REPORTED.items()])


def worklist(engine, stage_filter=None, limit=40):
    """Stalled episodes, most urgent first, each with an explained strategy."""
    out = []
    for ep in engine.episodes:
        st = status(ep)
        if st["stage"] == "closed" or st["overdue"] <= 0 or (stage_filter and st["stage"] != stage_filter):
            continue
        out.append((urgency(ep, st), ep))
    out.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [view(ep, engine.people[ep["person_id"]]) for _, ep in out[:limit]]


def scan(engine, at):
    """Continuous monitor: compares the loop with the last scan and raises an alert for each change that
    needs a person. Runs after every inbound event and on a timer, so nothing waits for someone to look."""
    abnormal, late = set(), set()
    for ep in engine.episodes:
        st = status(ep)
        if st["stage"] in ("resulted", "referred", "diagnosed") and ep["abnormal"]:
            abnormal.add(ep["id"])
            if st["overdue"] > 0:
                late.add(ep["id"])
    first = engine.monitor.get("abnormal") is None
    new_abn = abnormal - (engine.monitor.get("abnormal") or set())
    new_late = late - (engine.monitor.get("late") or set())
    worst = summary(engine)["worst"]
    alerts = []
    if first:
        alerts.append(dict(level="info", text=f"Monitoring started: {len(abnormal)} abnormal results open, {len(late)} past target; "
                                              f"biggest delay at '{worst['label']}'"))
    else:
        alerts += [dict(level="high", episode=i, text=f"New abnormal result {i}: refer for a diagnostic test within {SLA['resulted']} days")
                   for i in sorted(new_abn)]
        alerts += [dict(level="high", episode=i, text=f"Abnormal result {i} has passed its target") for i in sorted(new_late - new_abn)]
        if worst["stage"] != engine.monitor.get("worst"):
            alerts.append(dict(level="medium", text=f"Biggest delay moved to '{worst['label']}' ({worst['overdue']} past target)"))
    engine.monitor.update(abnormal=abnormal, late=late, worst=worst["stage"], last_scan=at)
    for a in alerts:
        a["at"] = at
    engine.alerts[:0] = alerts[::-1]
    del engine.alerts[50:]
    return alerts
