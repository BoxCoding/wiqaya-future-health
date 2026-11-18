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
                  appointment=None, result=None, resolution=None,
                  events=[_event("invited", invited_on, f"Invited for {g['pathway']}", source="Wiqaya plan")])
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
    ep.update(fields)
    ep["events"].append(_event(to, on, detail, source=source))


def close(ep, code, clinician, note, on=TODAY):
    """Clinician sign-off. Abnormal results need a diagnostic report first, unless a decline is documented."""
    s = stage(ep)
    if code not in RESOLUTIONS:
        raise LoopError(f"Unknown resolution '{code}'")
    if not clinician.strip():
        raise LoopError("A named clinician must sign off")
    if code == "patient-declined":
        if s not in ("resulted", "referred") or not ep["abnormal"]:
            raise LoopError("A decline is only recorded on an open abnormal result")
        if len(note.strip()) < 10:
            raise LoopError("Document the decline in the note (at least 10 characters)")
    elif s == "resulted" and not ep["abnormal"]:
        if code != "normal-recall":
            raise LoopError("A normal result closes with a routine recall")
    elif s == "diagnosed":
        if code == "normal-recall":
            raise LoopError("Choose the diagnostic outcome")
    else:
        raise LoopError(f"Episode {ep['id']} is '{s}'; it can only close after a result or diagnostic report")
    ep["resolution"] = dict(code=code, label=RESOLUTIONS[code], by=clinician.strip(), note=note.strip(), on=_day(on))
    ep["events"].append(_event("closed", on, RESOLUTIONS[code] + (f". {note.strip()}" if note.strip() else ""), by=clinician.strip()))


# ---------- views ----------
def view(ep, p):
    st = status(ep)
    return dict(id=ep["id"], person_id=p["id"], name=p["name"], age=p["age"], centre=p["centre"], pathway=ep["pathway"],
                abnormal=ep["abnormal"], result=ep["result"], resolution=ep["resolution"], events=ep["events"],
                status=st, stage_label=LABEL[st["stage"]], barriers=barriers(p),
                strategy=None if st["stage"] == "closed" else strategy(ep, p),
                approvals=ep.get("approvals", []))


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
    hide = lambda n: n if n >= MIN_CELL else None  # noqa: E731
    barrier_rows = []
    for b in ["Lives far from the clinic", "Often misses visits", "Aged 65+", "Reached through a guardian"]:
        with_b = [r for r in open_rows if b in barriers(r[2])]
        late = sum(r[1]["overdue"] > 0 for r in with_b)
        barrier_rows.append(dict(barrier=b, open=hide(len(with_b)), overdue=hide(late) if len(with_b) >= MIN_CELL else None,
                                 rate=round(late / len(with_b), 3) if len(with_b) >= MIN_CELL else None))
    centres = []
    for c in engine.centres:
        cr = [r for r in open_rows if r[2]["centre"] == c]
        late = sum(r[1]["overdue"] > 0 for r in cr)
        centres.append(dict(centre=c, open=hide(len(cr)), overdue=hide(late), rate=round(late / len(cr), 3) if len(cr) >= MIN_CELL else None))
    worst = max((s for s in stages if s["stage"] != "closed"), key=lambda s: s["overdue"])
    return dict(today=_day(TODAY), total=len(rows), open=len(open_rows), closed=len(closed), overdue=len(overdue),
                abnormal_open=len(abnormal_open), abnormal_overdue=sum(r[1]["overdue"] > 0 for r in abnormal_open),
                awaiting_signoff=sum(1 for r in open_rows if r[1]["waiting_for"] == "clinician sign-off"),
                median_days_to_close_abnormal=round(median(to_close)) if to_close else None,
                stages=stages, worst=worst, barriers=barrier_rows, centres=centres, sla=SLA)


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
