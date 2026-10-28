import os

import pytest

os.environ["WIQAYA_HOUSEHOLDS"] = "1500"

from fastapi.testclient import TestClient  # noqa: E402

from app import outreach, rules, safeguards  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    return Engine(n_households=1500, slots=60)


def adult(**kw):
    base = dict(age=42.0, sex="M", prediabetes=False, fam_crc_age=None, variant=None,
                years_since={"crc": None, "cervical": None, "breast": None, "hba1c": 0.5},
                consent=dict(clinical=True, genomic=True, family_history=True, context=True, outreach=True))
    return base | kw


def test_family_history_starts_colorectal_screening_earlier():
    assert not any("colorectal" in g["pathway"] for g in rules.care_gaps(adult()))
    gaps = rules.care_gaps(adult(fam_crc_age=48))
    assert any("colorectal" in g["pathway"] and "family history" in g["data"] for g in gaps)


def test_withheld_consent_is_not_used():
    no_fh = adult(fam_crc_age=48, consent=adult()["consent"] | {"family_history": False})
    assert not any("colorectal" in g["pathway"] for g in rules.care_gaps(no_fh))
    no_gen = adult(variant="BRCA1/2", consent=adult()["consent"] | {"genomic": False})
    assert not any(g["pathway"] == rules.GENOMIC_PATHWAY for g in rules.care_gaps(no_gen))


def test_recent_screen_is_not_a_gap():
    p = adult(sex="F", years_since={"crc": None, "cervical": 1.0, "breast": None, "hba1c": 0.5})
    assert not any("cervical" in g["pathway"] for g in rules.care_gaps(p))


def person(pid, role, age, opt_in=True, lang="en"):
    return dict(id=pid, role=role, name=f"P{pid}", age=age, language=lang, centre="C", family_opt_in=opt_in,
                consent=dict(outreach=True))


def test_adult_screening_stays_private_without_opt_in():
    hh = [person(0, "guardian", 35), person(1, "adult", 38, opt_in=False), person(2, "child", 4)]
    gaps = {0: [], 1: [{"pathway": "Adult screening: cervical"}], 2: [{"pathway": "Well-Child Visit"}]}
    msgs = outreach.compose(hh, gaps, {})
    family = [m for m in msgs if not m["private"]]
    assert len(family) == 1 and "P1" not in family[0]["text"] and "P2" in family[0]["text"]
    assert any(m["private"] and m["to_id"] == 1 for m in msgs)


def test_genomic_finding_never_named():
    hh = [person(0, "guardian", 40)]
    msgs = outreach.compose(hh, {0: [{"pathway": rules.GENOMIC_PATHWAY}]}, {})
    assert msgs and all("BRCA" not in m["text"] and m["private"] for m in msgs)


def test_small_cells_suppressed_with_complement():
    rows = [dict(segment=s, n=n, due=n, reach=0.5, suppressed=False) for s, n in [("a", 5), ("b", 40), ("c", 500)]]
    out = safeguards.suppress(rows)
    assert [r["suppressed"] for r in out] == [True, True, False]
    assert out[0]["n"] is None and out[2]["n"] == 500


def test_plan_respects_capacity_and_keeps_households_whole(engine):
    for centre in engine.centres:
        plan = engine.plans[centre]
        assert sum(len(h["members"]) for h in plan) <= engine.slots
        for h in plan:
            due_ids = {p["id"] for p in engine.due(centre) if p["household"] == h["household"]}
            assert {m["id"] for m in h["members"]} == due_ids


def test_benefit_ranking_beats_calendar_rule(engine):
    pol = engine.outcomes()["policies"]
    # The ranking optimises risk × lift, so early findings caused is the metric it must win on.
    w, c = pol["Wiqaya: benefit ranking"], pol["Calendar rule: most overdue first"]
    assert w["early_findings_caused"] > c["early_findings_caused"]


def test_bias_correction_scores_never_screened_closer_to_truth(engine):
    b = engine.bias_check()
    assert abs(b["corrected_model"] - b["true_risk"]) < abs(b["naive_model"] - b["true_risk"])


def test_api_round_trip():
    with TestClient(app) as c:
        centre = c.get("/api/overview").json()["centres"][0]
        member = c.get(f"/api/plan/{centre}").json()["households"][0]["members"][0]
        view = c.get(f"/api/person/{member['id']}").json()
        assert "_truth" not in view["person"]
        ok = c.post("/api/decisions", json={"person_id": member["id"], "pathway": member["pathways"][0], "action": "accept"})
        assert ok.status_code == 201
        assert c.post("/api/decisions", json={"person_id": member["id"], "pathway": "Made up", "action": "accept"}).status_code == 422
        assert c.get("/api/person/999999").status_code == 404
        assert c.get("/api/safeguards", params={"dimension": "bogus"}).status_code == 422


def test_adolescent_gets_confidential_message_younger_child_does_not():
    hh = [person(0, "guardian", 45), person(1, "child", 15) | {"own_contact": True},
          person(2, "child", 9) | {"own_contact": False}]
    gaps = {0: [], 1: [{"pathway": "Well-Child Visit"}], 2: [{"pathway": "Well-Child Visit"}]}
    msgs = outreach.compose(hh, gaps, {})
    assert [m["to_id"] for m in msgs if m["private"]] == [1]
    assert all("confidence" not in m["text"] for m in msgs if not m["private"])


def test_outreach_is_english_and_arabic_only():
    assert set(outreach.TEMPLATES) == {"en", "ar"}


def test_ai_endpoints_are_rate_limited(monkeypatch):
    import app.main as main
    monkeypatch.setenv("WIQAYA_LLM", "off")
    monkeypatch.setattr(main, "ASK_LIMIT", 2)
    main._asks.clear()
    with TestClient(app) as c:
        codes = [c.post("/api/agent/ask", json={"question": "children in Al Ain"}).status_code for _ in range(3)]
    main._asks.clear()
    assert codes == [200, 200, 429]


def test_person_view_drops_consent_withheld_fields(engine):
    p = next(p for p in engine.people if not p["consent"]["genomic"] and not p["consent"]["context"])
    person = engine.person_view(p["id"])["person"]
    assert "variant" not in person and "distance_km" not in person and "_truth" not in person


def test_safeguard_means_hidden_over_small_subgroups(engine):
    members = [p for p in engine.people if p["id"] in engine.priority][:40]
    row = safeguards._row(engine, "x", members, 0.5)
    high = [p for p in members if p["_truth"]["risk"] >= safeguards.HIGH_RISK]
    assert (row["high_risk_reach"] is None) == (len(high) < safeguards.MIN_CELL)
    assert safeguards._cell_mean([True] * 9) is None and safeguards._cell_mean([True] * 10) == 1.0
