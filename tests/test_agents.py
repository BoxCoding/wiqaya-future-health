import pytest

from app import agents, llm
from app.engine import Engine


@pytest.fixture(scope="module")
def engine():
    return Engine(n_households=1500, slots=60)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("WIQAYA_LLM", "off")


def test_rule_parser_reads_the_question():
    spec, pid = agents.parse_rules("Emirati women in Al Ain under 50 overdue cervical screening, not invited, by region")
    assert pid is None
    assert (spec["nationality"], spec["sex"], spec["region"], spec["age_max"]) == ("Emirati", "F", "Al Ain", 50)
    assert spec["pathway"] == "Adult screening: cervical" and spec["in_plan"] is False and spec["group_by"] == "region"
    assert agents.parse_rules("Brief me on person 42")[1] == 42


def test_verifier_drops_filters_the_question_does_not_support():
    q = "children under 5 in Al Ain"
    assert agents._evidence("region", "Al Ain", q)
    assert not agents._evidence("nationality", "Arab expatriate", q)
    assert not agents._evidence("age_min", 0, "children in Al Ain")
    assert not agents._evidence("sex", "M", q)


FACTS = {"matches": "104 people with a care gap match (Al Ain, under 5).", "in_plan": "31 of them are in this week's plan."}


@pytest.mark.parametrize("lead, problem", [
    ("There are 104 children due.", "states a number"),
    ("Two groups stand out.", "states a number"),
    ("Here is {matches}.", "placeholder"),
    ("Abu Dhabi City leads the way.", "names a place"),
    ("Arab expatriate families are behind.", "names a place"),
    ("Rajesh is following up.", "names a person"),
    ("Most have completed screening.", "states a number"),
    ("Screening is improving.", "adds a claim"),
    ("These children urgently need care.", "alarmist"),
])
def test_safety_agent_rejects_leads_that_state_facts(lead, problem):
    issues = agents.lead_check(lead, allowed_literals="Al Ain, under 5")
    assert any(problem in i for i in issues), issues


def test_clinical_instructions_blocked_only_in_patient_briefs():
    assert any("clinical instruction" in i for i in agents.lead_check("The patient requires a follow-up.", strict=True))
    assert any("clinical instruction" in i for i in agents.lead_check("She must be seen.", strict=True))
    assert agents.lead_check("Certain segments require adjustments to ensure equitable engagement.") == []


def test_answer_is_assembled_from_verified_sentences_only():
    assert agents.lead_check("Here is where developmental checks stand in Al Ain.", "Al Ain, under 5") == []
    assert agents.lead_check("Here is how mothers and other carers are doing.") == []
    assert agents.lead_check("A look at outstanding screening requirements.") == []  # the noun is fine  # "other" is not the place "Other"
    answer, order = agents.compose("Here is the picture.", ["in_plan", "bogus", "in_plan"], FACTS, required=["matches"])
    assert order == ["matches", "in_plan"]
    assert answer == "Here is the picture. 104 people with a care gap match (Al Ain, under 5). 31 of them are in this week's plan."


def test_one_revision_then_fallback(monkeypatch):
    replies = iter([{"lead": "There are 999 children.", "use": ["matches"]}, {"lead": "Here is the picture.", "use": ["in_plan"]}])
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: next(replies))
    trace = agents.Trace()
    text, source, meta = agents.write_checked(trace, "Narrator", "sys", FACTS, {}, required=["matches"])
    assert meta["used"] == ["matches", "in_plan"] and meta["picked"] == ["in_plan"] and meta["lead"] == "Here is the picture."
    assert source == "llm" and text.startswith("Here is the picture. 104 people")
    assert [s["action"] for s in trace.steps] == ["Drafted", "Rejected", "Revised", "Approved"]

    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"lead": "Exactly seven children.", "use": []})
    text, source, meta = agents.write_checked(agents.Trace(), "Narrator", "sys", FACTS, {})
    assert meta["lead"] is None and meta["used"] == list(FACTS)
    assert (text, source) == (" ".join(FACTS.values()), "rules")


def test_offline_copilot_answers_from_the_engine(engine):
    r = agents.ask(engine, "How many children under 5 in Al Ain are overdue a developmental check?")
    truth = engine.query(dict(region="Al Ain", age_max=5.0, pathway="Well-Child Visit + developmental assessment"))
    assert r["intent"] == "population_query" and r["source"] == "rules"
    assert f"{truth['count']:,}" in r["answer"] and r["answer"].endswith(agents.DISCLAIMER)
    assert r["trace"][0]["agent"] == "Supervisor"


def test_offline_case_brief_and_safeguards(engine):
    assert agents.ask(engine, "Brief me on person 5")["intent"] == "case_brief"
    assert agents.ask(engine, "Is any group left behind?")["intent"] == "safeguards"
    assert "no person" in agents.ask(engine, "Brief me on person 9999999")["answer"]
