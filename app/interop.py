"""EHR interoperability: FHIR R4 in and out, HL7 v2 ORU^R01 lab results in.

Inbound messages only move an episode forward along the care loop (app/loop.py); they never close it.
Closing stays a clinician action. Every message is validated against the patient and an open episode
before anything changes, and each entry gets its own outcome so one bad entry never hides another.

shortcut: pathway codes use a local code system; map them to the DoH-approved LOINC/SNOMED sets before a pilot.
"""
from datetime import date

from . import loop
from .engine import public_person

PATHWAY_SYSTEM = "https://wiqaya.health/fhir/CodeSystem/pathway"
FLAG_ABNORMAL = {"A", "AA", "H", "HH", "L", "LL", "POS", "DET"}
FLAG_NORMAL = {"N", "NEG", "ND"}


class InteropError(ValueError):
    pass


def _pid(ref, n_people):
    """'Patient/123' or '123' → 123, validated against the population."""
    raw = str(ref or "").split("/")[-1]
    if not raw.isdigit() or not 0 <= int(raw) < n_people:
        raise InteropError(f"Unknown patient reference '{ref}'")
    return int(raw)


def _open_episode(engine, pid, res=None):
    """The episode named in basedOn (Task/E00001 or ServiceRequest/E00001), else the patient's latest open one."""
    ref = next((b.get("reference", "") for b in (res or {}).get("basedOn", []) if isinstance(b, dict)), "")
    if ref:
        ep = engine.episode.get(ref.split("/")[-1])
        if ep is None or ep["person_id"] != pid:
            raise InteropError(f"basedOn '{ref}' is not an episode of patient {pid}")
        if loop.stage(ep) == "closed":
            raise InteropError(f"Episode {ep['id']} is already closed")
        return ep
    open_eps = [ep for ep in engine.episodes_of[pid] if loop.stage(ep) != "closed"]
    if not open_eps:
        raise InteropError(f"Patient {pid} has no open care episode")
    return open_eps[-1]


def _on(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else loop.TODAY
    except ValueError as e:
        raise InteropError(f"Bad date '{value}'") from e


def apply_resource(engine, res):
    """One FHIR resource → one loop transition. Returns a short description of what changed."""
    rt = res.get("resourceType")
    n = len(engine.people)
    if rt == "Appointment":
        actor = next((a.get("actor", {}).get("reference") for a in res.get("participant", [])
                      if "Patient/" in str(a.get("actor", {}).get("reference", ""))), None)
        ep = _open_episode(engine, _pid(actor, n), res)
        when = _on(res.get("start"))
        st = res.get("status")
        if st == "booked":
            loop.advance(ep, "booked", when, f"Appointment {when:%d %b}", appointment=when.isoformat())
        elif st == "fulfilled":
            loop.advance(ep, "attended", when, "Screening visit completed", source="FHIR Appointment")
        elif st == "noshow":
            loop.advance(ep, "booked", when, "Missed appointment (no-show)", appointment=None)
        else:
            raise InteropError(f"Appointment status '{st}' is not tracked")
    elif rt == "Encounter":
        if res.get("status") != "finished":
            raise InteropError("Only finished encounters are tracked")
        ep = _open_episode(engine, _pid(res.get("subject", {}).get("reference"), n), res)
        loop.advance(ep, "attended", _on(res.get("period", {}).get("end")), "Screening visit completed")
    elif rt == "Observation":
        ep = _open_episode(engine, _pid(res.get("subject", {}).get("reference"), n), res)
        flags = {c.get("code", "").upper() for i in res.get("interpretation", []) for c in i.get("coding", [])}
        if not flags & (FLAG_ABNORMAL | FLAG_NORMAL):
            raise InteropError("Observation needs an interpretation (e.g. N or A)")
        abnormal = bool(flags & FLAG_ABNORMAL)
        text = res.get("valueString") or ("Abnormal: further tests needed" if abnormal else "Normal")
        loop.advance(ep, "resulted", _on(res.get("effectiveDateTime") or res.get("issued")), text,
                     source=res.get("_source", "FHIR Observation"), abnormal=abnormal,
                     result=dict(flag="A" if abnormal else "N", text=text))
    elif rt == "ServiceRequest":
        ep = _open_episode(engine, _pid(res.get("subject", {}).get("reference"), n), res)
        loop.advance(ep, "referred", _on(res.get("authoredOn")), "Referred for diagnostic test")
    elif rt == "DiagnosticReport":
        ep = _open_episode(engine, _pid(res.get("subject", {}).get("reference"), n), res)
        confirmed = any(c.get("code") in ("confirmed", "positive") for cc in res.get("conclusionCode", [])
                        for c in cc.get("coding", []))
        loop.advance(ep, "diagnosed", _on(res.get("issued") or res.get("effectiveDateTime")),
                     res.get("conclusion") or ("Diagnosis confirmed" if confirmed else "Diagnostic test negative"), confirmed=confirmed)
    else:
        raise InteropError(f"Resource type '{rt}' is not accepted")
    return ep["id"], f"{ep['id']} → {loop.stage(ep)}"


def ingest_bundle(engine, bundle):
    """FHIR transaction/batch bundle → (OperationOutcome-style result with one issue per entry, changed episode ids)."""
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise InteropError("Body must be a FHIR Bundle")
    entries = bundle.get("entry") or []
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise InteropError("A bundle carries 1 to 100 entries")
    issues, changed = [], []
    for i, e in enumerate(entries):
        res = e.get("resource") if isinstance(e, dict) else None
        try:
            if not isinstance(res, dict):
                raise InteropError("Entry has no resource")
            eid, text = apply_resource(engine, res)
            changed.append(eid)
            issues.append(dict(severity="information", code="informational", entry=i, diagnostics=text))
        except (InteropError, loop.LoopError) as err:
            issues.append(dict(severity="error", code="processing", entry=i, diagnostics=str(err)))
    return dict(resourceType="OperationOutcome", issue=issues), changed


# ---------- HL7 v2 ----------
def parse_oru(text):
    """Minimal ORU^R01 reader: MSH, PID-3, OBR-2 (placer order = episode id), OBX-3/5/8/14. Returns (control_id, pid, observation dict)."""
    segs = [s for s in text.replace("\n", "\r").split("\r") if s.strip()]
    if not segs or not segs[0].startswith("MSH|"):
        raise InteropError("Message must start with an MSH segment")
    fields = {s[:3]: s.split("|") for s in segs}
    msh = fields["MSH"]
    if len(msh) < 12 or not msh[8].startswith("ORU^R01"):
        raise InteropError("Only ORU^R01 result messages are accepted")
    if "PID" not in fields or "OBX" not in fields:
        raise InteropError("ORU^R01 needs PID and OBX segments")
    pid_raw = fields["PID"][3].split("^")[0] if len(fields["PID"]) > 3 else ""
    obx = fields["OBX"]
    if len(obx) < 9:
        raise InteropError("OBX segment is incomplete")
    flag = obx[8].upper()
    if flag not in FLAG_ABNORMAL | FLAG_NORMAL:
        raise InteropError(f"OBX-8 abnormal flag '{obx[8]}' is not recognised")
    when = obx[14][:8] if len(obx) > 14 and len(obx[14]) >= 8 else ""
    obr = fields.get("OBR", [])
    placer = obr[2].split("^")[0] if len(obr) > 2 else ""
    obs = dict(resourceType="Observation", _source="HL7 ORU^R01", subject=dict(reference=f"Patient/{pid_raw}"),
               basedOn=[dict(reference=f"ServiceRequest/{placer}")] if placer else [],
               interpretation=[dict(coding=[dict(code=flag)])], valueString=obx[5] or None,
               effectiveDateTime=f"{when[:4]}-{when[4:6]}-{when[6:8]}" if when else None)
    return msh[9], pid_raw, obs


def ingest_hl7(engine, text):
    """Returns (HL7 ACK text, changed episode id or None); AA when applied, AE with the reason when not."""
    control = ""
    try:
        control, _, obs = parse_oru(text)
        eid, detail = apply_resource(engine, obs)
        return ack(control, "AA", detail), eid
    except (InteropError, loop.LoopError) as err:
        return ack(control, "AE", str(err)), None


def ack(control, code, text):
    stamp = f"{loop.TODAY:%Y%m%d}0900"
    return f"MSH|^~\\&|WIQAYA|DOH-AD|LAB|PHC|{stamp}||ACK^R01|ACK{control}|P|2.5.1\rMSA|{code}|{control}|{text.replace('|', '/')}"


def sample_oru(engine, ep, abnormal, control):
    p = engine.people[ep["person_id"]]
    flag, value = ("A", "Abnormal: further tests needed") if abnormal else ("N", "Normal")
    stamp = f"{loop.TODAY:%Y%m%d}"
    return "\r".join([
        f"MSH|^~\\&|LIS|{p['centre'].replace(' ', '')}|WIQAYA|DOH-AD|{stamp}0830||ORU^R01|{control}|P|2.5.1",
        f"PID|1||{p['id']}^^^WIQAYA||{p['name']}^Synthetic",
        f"OBR|1|{ep['id']}||{ep['pathway']}",
        f"OBX|1|ST|{ep['pathway']}||{value}|||{flag}|||F|||{stamp}0815",
    ])


# ---------- FHIR export ----------
def patient_bundle(engine, pid):
    """FHIR R4 searchset: the Patient (consent-filtered) and, per episode, a Task carrying the loop state."""
    p = public_person(engine.people[pid])
    patient = dict(resourceType="Patient", id=str(pid), name=[dict(given=[p["name"]], text=p["name"])],
                   gender={"F": "female", "M": "male"}[p["sex"]],
                   communication=[dict(language=dict(coding=[dict(system="urn:ietf:bcp:47", code=p["language"])]))],
                   address=[dict(district=p["region"], country="AE")])
    entries = [patient]
    for ep in engine.episodes_of[pid]:
        st = loop.status(ep)
        entries.append(dict(
            resourceType="Task", id=ep["id"], status="completed" if st["stage"] == "closed" else "in-progress", intent="plan",
            code=dict(coding=[dict(system=PATHWAY_SYSTEM, display=ep["pathway"])]), businessStatus=dict(text=loop.LABEL[st["stage"]]),
            **{"for": dict(reference=f"Patient/{pid}")}, authoredOn=ep["events"][0]["on"], lastModified=ep["events"][-1]["on"],
            output=[dict(type=dict(text="resolution"), valueString=ep["resolution"]["label"])] if ep["resolution"] else [],
            note=[dict(text=f"{e['on']} · {loop.LABEL[e['stage']]} · {e['source']} · {e['detail']}") for e in ep["events"]]))
        if ep["result"]:
            entries.append(dict(resourceType="Observation", id=f"{ep['id']}-result", status="final",
                                code=dict(coding=[dict(system=PATHWAY_SYSTEM, display=ep["pathway"])]),
                                subject=dict(reference=f"Patient/{pid}"), valueString=ep["result"]["text"],
                                interpretation=[dict(coding=[dict(system="http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                                                                  code=ep["result"]["flag"])])]))
    return dict(resourceType="Bundle", type="searchset", total=len(entries),
                entry=[dict(fullUrl=f"urn:wiqaya:{r['resourceType']}/{r['id']}", resource=r) for r in entries])
