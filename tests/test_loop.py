import os

import pytest

os.environ["WIQAYA_HOUSEHOLDS"] = "1500"
os.environ["WIQAYA_LLM"] = "off"

from fastapi.testclient import TestClient  # noqa: E402

from app import agents, auth, interop, loop  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.main import app  # noqa: E402

TOKEN = "test-ingest-token"


@pytest.fixture(scope="module")
def engine():
    return Engine(1500)


def first(engine, stage, abnormal=None):
    return next(ep for ep in engine.episodes if loop.stage(ep) == stage and (abnormal is None or ep["abnormal"] == abnormal))


def test_simulated_episodes_follow_the_stage_order(engine):
    order = {s: i for i, s in enumerate(loop.STAGES)}
    for ep in engine.episodes:
        idx = [order[e["stage"]] for e in ep["events"]]
        assert idx == sorted(idx)
        assert (loop.stage(ep) == "closed") == (ep["resolution"] is not None)
        if ep["resolution"]:
            assert ep["events"][-1]["by"], "every closure names a clinician"


def test_abnormal_result_cannot_close_without_diagnostic(engine):
    ep = first(engine, "resulted", abnormal=True)
    with pytest.raises(loop.LoopError):
        loop.close(ep, "diagnostic-negative", "Dr. Test", "")
    with pytest.raises(loop.LoopError):
        loop.close(ep, "patient-declined", "Dr. Test", "no")  # decline needs a documented note
    with pytest.raises(loop.LoopError):
        loop.close(ep, "normal-recall", "", "")


def test_transitions_only_move_forward(engine):
    ep = first(engine, "invited")
    with pytest.raises(loop.LoopError):
        loop.advance(ep, "resulted", loop.TODAY, "skip")


def test_worklist_is_overdue_and_explained(engine):
    work = loop.worklist(engine)
    assert work and all(w["status"]["overdue"] > 0 and w["strategy"]["action"] and w["strategy"]["why"] for w in work)
    assert work[0]["abnormal"], "abnormal results come first"


def test_summary_suppresses_small_groups(engine):
    s = loop.summary(engine)
    for row in s["barriers"] + s["centres"]:
        assert row["rate"] is None or (row["open"] or 0) >= 10


def test_hl7_result_moves_episode_and_bad_messages_are_rejected(engine):
    ep = first(engine, "attended")
    ack, eid = interop.ingest_hl7(engine, interop.sample_oru(engine, ep, True, "T1"))
    assert "MSA|AA|T1" in ack and eid == ep["id"] and loop.stage(ep) == "resulted" and ep["abnormal"]
    ack, detail = interop.ingest_hl7(engine, "MSH|^~\\&|X|Y|Z|W|20261026||ADT^A01|T2|P|2.5.1")
    assert "MSA|AE" in ack and detail is None


def test_fhir_bundle_reports_each_entry(engine):
    ep = first(engine, "invited")
    pid = ep["person_id"]
    out, ids = interop.ingest_bundle(engine, {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Appointment", "status": "booked", "start": "2026-10-28T09:00:00",
                      "basedOn": [{"reference": f"ServiceRequest/{ep['id']}"}],
                      "participant": [{"actor": {"reference": f"Patient/{pid}"}}]}},
        {"resource": {"resourceType": "Observation", "subject": {"reference": "Patient/999999"}}},
    ]})
    sev = [i["severity"] for i in out["issue"]]
    assert sev == ["information", "error"] and ids == [ep["id"]]
    assert loop.stage(ep) == "booked"


def test_fhir_export_respects_consent(engine):
    p = next(p for p in engine.people if not p["consent"]["context"] and engine.episodes_of[p["id"]])
    bundle = interop.patient_bundle(engine, p["id"])
    assert "distance_km" not in str(bundle)
    assert any(e["resource"]["resourceType"] == "Task" for e in bundle["entry"])


def test_bottleneck_analyst_answers_offline(engine):
    r = agents.bottlenecks(engine)
    assert r["source"] == "rules" and "abnormal results are still open" in r["answer"]
    assert agents.ask(engine, "Where are patients stalling after the invitation?")["intent"] == "bottlenecks"


def test_api_ingest_requires_token_and_clinician_closes(monkeypatch):
    with TestClient(app) as c:
        monkeypatch.delenv("WIQAYA_INGEST_TOKEN", raising=False)
        assert c.post("/api/hl7", content="MSH|").status_code == 503
        monkeypatch.setenv("WIQAYA_INGEST_TOKEN", TOKEN)
        assert c.post("/api/hl7", content="MSH|", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = c.post("/api/hl7", content="MSH|", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200 and "MSA|AE" in r.text
        e = app.state.engine
        ep = first(e, "diagnosed")
        c.post("/api/auth/login", json={"username": "dr.amna", "password": auth.DEMO_PASSWORD})
        bad = c.post(f"/api/episodes/{ep['id']}/close", json={"resolution": "normal-recall"})
        assert bad.status_code == 409
        ok = c.post(f"/api/episodes/{ep['id']}/close", json={"resolution": "diagnostic-negative"})
        assert ok.status_code == 200 and ok.json()["status"]["stage"] == "closed"
        assert ok.json()["resolution"]["by"] == "Dr. Amna Al Mansoori", "the signer comes from the session, not the form"
        c.post("/api/auth/guest", json={"role": "coordinator"})
        assert c.post(f"/api/episodes/{ep['id']}/outreach", json={"decision": "approve"}).status_code == 409
        assert c.get("/api/loop/worklist", params={"stage": "bogus"}).status_code == 422
        demo = c.post("/api/feed/demo").json()
        assert "ORU^R01" in demo["message"] and "MSA|AA" in demo["ack"]


def test_roles_limit_what_each_user_can_see_and_do():
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "dr.amna", "password": "wrong"}).status_code == 401
        c.post("/api/auth/login", json={"username": "omar.planner", "password": auth.DEMO_PASSWORD})
        assert c.get("/api/loop").status_code == 200
        assert c.get("/api/person/1").status_code == 403, "planners never see named individuals"
        assert c.get("/api/loop/worklist").status_code == 403
        ask = c.post("/api/agent/ask", json={"question": "Adults over 45 in Al Dhafra due colorectal screening"}).json()
        assert ask["facts"]["result"]["top"] == []
        assert c.get("/api/audit").status_code == 403
        c.post("/api/auth/guest", json={"role": "coordinator"})
        ep = first(app.state.engine, "resulted", abnormal=True)
        assert c.post(f"/api/episodes/{ep['id']}/close", json={"resolution": "patient-declined", "note": "x" * 20}).status_code == 403
        c.post("/api/auth/logout")
        assert c.get("/api/loop").status_code == 401


def test_tampered_session_cookie_is_rejected():
    good = auth.sign(auth.session_for("omar.planner", "Omar", "planner"))
    body, mac = good.split(".")
    forged = auth.sign(auth.session_for("x", "x", "admin")).split(".")[0] + "." + mac
    assert auth.unsign(good)["role"] == "planner" and auth.unsign(forged) is None


def test_audit_log_records_access_and_detects_tampering():
    with TestClient(app) as c:
        c.post("/api/auth/guest", json={"role": "clinician"})
        c.get("/api/person/3")
        c.post("/api/auth/login", json={"username": "privacy.admin", "password": auth.DEMO_PASSWORD})
        log = c.get("/api/audit").json()
        assert log["chain_ok"] and any(r["action"] == "person.view" and r["target"] == "3" for r in log["rows"])
        st = app.state.store
        st.db.execute("UPDATE audit SET target = '999' WHERE action = 'person.view'")
        assert not st.verify()


def test_changes_survive_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("WIQAYA_DB", str(tmp_path / "w.db"))
    with TestClient(app) as c:
        c.post("/api/auth/guest", json={"role": "coordinator"})
        eid = first(app.state.engine, "invited")["id"]
        assert c.post(f"/api/episodes/{eid}/barriers", json={"code": "transport", "note": "no car"}).status_code == 200
    with TestClient(app) as c:  # a fresh engine replays the saved change
        c.post("/api/auth/guest", json={"role": "coordinator"})
        ep = c.get(f"/api/episodes/{eid}").json()
        assert ep["reported"][0]["code"] == "transport"
        assert ep["strategy"]["action"].startswith("Arrange transport") and "Patient reported" in ep["strategy"]["why"]


def test_monitor_alerts_on_a_new_abnormal_result(engine):
    loop.scan(engine, "t0")
    ep = first(engine, "attended")
    interop.ingest_hl7(engine, interop.sample_oru(engine, ep, True, "M1"))
    alerts = loop.scan(engine, "t1")
    assert any(a.get("episode") == ep["id"] and a["level"] == "high" for a in alerts)
