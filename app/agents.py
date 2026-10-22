"""Wiqaya multi-agent copilot. The LLM proposes; deterministic code verifies and executes.

  Supervisor ──► Query planner ─► Verifier ─► Analyst tool ─► Narrator ─┐
             ├─► Case reviewer + Outreach checker ─────────────────────┼─► Safety & grounding ─► answer
             └─► Safeguards reporter ──────────────────────────────────┘

Every step lands in a trace the UI shows. Any LLM failure, invalid output or rejected draft falls
back to a deterministic path, so the copilot always answers and never states an ungrounded number.
"""
import json
import re
import time

from . import llm, safeguards
from .data import NAMES, NATIONALITIES, REGIONS
from .rules import PATHWAYS

DISCLAIMER = "Population prioritisation, not a diagnosis; the clinician decides."
INTENTS = ["population_query", "case_brief", "safeguards"]

SUPERVISOR_SYS = ("You route questions for Wiqaya, a preventive-care planner in Abu Dhabi. Pick population_query for "
                  "counts of people due by area, age or pathway; case_brief for one specific person; safeguards for "
                  "fairness, reach or bias across groups.")
PLANNER_SYS = ("Convert the planner's question into filters over people with a care gap. Use null for anything the "
               "question does not state. Ages are in years.")
NARRATOR_SYS = "You are Wiqaya's population analyst answering a public-health planner."
BRIEF_SYS = ("You are Wiqaya's case reviewer briefing a clinician calmly. This is prioritisation, never a diagnosis; "
             "include the uncertainty.")
SAFEGUARDS_SYS = "You are Wiqaya's safeguards reporter explaining fairness of outreach across groups."

SPEC_FIELDS = ["region", "nationality", "sex", "age_min", "age_max", "pathway", "in_plan", "group_by"]
SPEC_SCHEMA = {"type": "object", "required": SPEC_FIELDS, "properties": {
    "region": {"enum": [*REGIONS, None]}, "nationality": {"enum": [*NATIONALITIES, None]},
    "sex": {"enum": ["F", "M", None]}, "age_min": {"type": ["number", "null"]}, "age_max": {"type": ["number", "null"]},
    "pathway": {"enum": [*PATHWAYS, None]}, "in_plan": {"type": ["boolean", "null"]},
    "group_by": {"enum": [*safeguards.DIMENSIONS, None]}}}
INTENT_SCHEMA = {"type": "object", "required": ["intent"], "properties": {"intent": {"enum": INTENTS}}}

PATHWAY_WORDS = [(r"develop", PATHWAYS[1]), (r"well[- ]?child|\bwcv\b", PATHWAYS[0]), (r"colorectal|bowel|colon", PATHWAYS[2]),
                 (r"cervical|pap\b|smear", PATHWAYS[3]), (r"breast|mammo", PATHWAYS[4]), (r"diabet|hba1c", PATHWAYS[5]),
                 (r"genetic|genomic|brca|counsell", PATHWAYS[6])]
REGION_WORDS = [(r"al ain", "Al Ain"), (r"dhafra|western region", "Al Dhafra"), (r"abu dhabi city|\bcapital\b", "Abu Dhabi City")]
NATIONALITY_WORDS = [(r"emirati", "Emirati"), (r"arab expat", "Arab expatriate"), (r"south asian", "South Asian"),
                     (r"filipin", "Filipino"), (r"\bwestern\b(?! region)", "Western")]
GROUP_WORDS = {"nationality": "nationality", "region": "region", "sex": "sex", "gender": "sex", "language": "language",
               "age": "age band"}
SAFEGUARD_HINT = re.compile(r"safeguard|fair|bias|equit|gate|segment|under-?served|left behind")
BANNED = re.compile(r"\b(diagnos\w*|you have|has (?:cancer|diabetes)|definitely|certainly|guarantee\w*|cure[sd]?|"
                    r"urgent\w*|immediate\w*|emergenc\w*|alarming)\b", re.I)


class Trace:
    def __init__(self):
        self.steps, self._last = [], time.perf_counter()

    def add(self, agent, action, detail, source="rules"):
        now = time.perf_counter()
        self.steps.append(dict(agent=agent, action=action, detail=str(detail), source=source,
                               ms=round((now - self._last) * 1000)))
        self._last = now


# ---------- Rule layer: fast, deterministic understanding of the question ----------
def parse_rules(question):
    t = question.lower()
    spec = {}
    for table, key in ((REGION_WORDS, "region"), (NATIONALITY_WORDS, "nationality")):
        spec[key] = next((v for pat, v in table if re.search(pat, t)), None)
    spec["pathway"] = next((v for pat, v in PATHWAY_WORDS if re.search(pat, t)), None)
    spec["sex"] = "F" if re.search(r"\b(women|female|mothers|girls)\b", t) else "M" if re.search(r"\b(men|male|fathers|boys)\b", t) else None
    under, over = re.search(r"(?:under|below|younger than)\s+(\d+)", t), re.search(r"(?:over|above|older than|aged)\s+(\d+)", t)
    spec["age_max"] = float(under.group(1)) if under else None
    spec["age_min"] = float(over.group(1)) if over else None
    if not (under or over):
        for pat, lo, hi in ((r"infant|bab(y|ies)", None, 2), (r"teen|adolescent", 13, 18), (r"child|kids", None, 18),
                            (r"elder|senior|older adult", 65, None)):
            if re.search(pat, t):
                spec["age_min"], spec["age_max"] = lo, hi
                break
    spec["in_plan"] = (False if re.search(r"not (?:been )?invited|left out|missed|not in (?:the|this week'?s) plan", t)
                       else True if re.search(r"\binvited\b|this week|in the plan", t) else None)
    by = re.search(r"\bby (nationality|region|sex|gender|language|age)", t)
    spec["group_by"] = GROUP_WORDS[by.group(1)] if by else None
    pid = re.search(r"(?:person|patient|id)\s*#?\s*(\d+)|#(\d+)", t)
    return spec, int(next(g for g in pid.groups() if g)) if pid else None


def _evidence(field, value, question):
    """Would a careful reader find this LLM-proposed filter in the question itself?"""
    t = question.lower()
    if field in ("region", "nationality"):
        return value.lower() in t
    if field == "pathway":
        return any(re.search(pat, t) for pat, v in PATHWAY_WORDS if v == value)
    if field in ("age_min", "age_max"):
        return 0 <= value <= 110 and str(int(value)) in t
    if field == "group_by":
        return f"by {value.split()[0]}" in t
    return False  # sex, in_plan: only trusted from the rule layer


# ---------- Safety & grounding agent ----------
# Writers never state facts. Code pre-writes every fact as a verified sentence; the model returns JSON with
# a fact-free lead sentence and which verified sentences answer the question, in order. Code assembles the
# answer, so grammar and figures are guaranteed and the model can't invent or re-label anything.
DIGIT = re.compile(r"\d")
NUMBER_WORDS = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozens?|hundreds?|"
                          r"thousands?|half|quarter|majority|most)\b", re.I)
PLACES = [*REGIONS, *NATIONALITIES, "PHC"]
PEOPLE = sorted({n for pools in NAMES.values() for names in pools.values() for n in names})
CLAIMS = re.compile(r"\b(percent|per cent|complet\w*|target\w*|leading|managed|responsible|improv\w*|declin\w*|trend\w*|recommend\w*)\b", re.I)
DIRECTIVES = re.compile(r"\b(requires?|required|must|needs to)\b", re.I)  # clinical orders: banned only in a patient brief
WRITER_SCHEMA = {"type": "object", "required": ["lead", "use"],
                 "properties": {"lead": {"type": "string"}, "use": {"type": "array", "items": {"type": "string"}}}}
WRITER_RULES = ("Reply as JSON. 'lead' is ONE short sentence that frames the answer in plain words and states no facts: "
                "no digits, number words, names, places or percentages. 'use' lists, best first, the keys of the "
                "verified sentences in 'facts' that answer the question.")


def lead_check(lead, allowed_literals="", strict=False):
    issues = []
    if not lead.strip():
        issues.append("empty lead")
    if m := DIGIT.search(lead) or NUMBER_WORDS.search(lead):
        issues.append(f"states a number: '{m.group(0)}'")
    if "{" in lead or "}" in lead:
        issues.append("contains a placeholder")
    if places := [p for p in PLACES if re.search(rf"\b{re.escape(p)}\b", lead) and p not in allowed_literals]:
        issues.append(f"names a place: {', '.join(places)}")
    if names := [n for n in PEOPLE if re.search(rf"\b{n}\b", lead)]:
        issues.append(f"names a person: {', '.join(names)}")
    if m := CLAIMS.search(lead):
        issues.append(f"adds a claim the data does not make: '{m.group(0)}'")
    if m := BANNED.search(lead):
        issues.append(f"diagnostic or alarmist language: '{m.group(0)}'")
    if strict and (m := DIRECTIVES.search(lead)):
        issues.append(f"gives a clinical instruction: '{m.group(0)}'")
    if len(lead) > 160:
        issues.append("lead too long")
    return issues


def sentence(phrase):
    return f"{phrase[0].upper()}{phrase[1:]}."


def compose(lead, use, facts, required):
    """The model chooses which facts; they always read in their natural order, and required ones are never dropped."""
    order = [k for k in facts if k in use or k in required]
    return " ".join([lead.strip(), *(facts[k] for k in order)]), order


def write_checked(trace, agent, system, facts, context, required=(), allowed_literals="", strict=False):
    """facts: key -> verified sentence. The model picks and frames; the safety agent checks the lead; one revision.
    Returns (answer, source, meta) where meta says which facts were used and the model's lead, for the UI."""
    fallback = " ".join(facts.values())
    rules_meta = dict(lead=None, used=list(facts), picked=[], facts=facts)
    payload = json.dumps(dict(context=context, facts=facts), ensure_ascii=False)
    system = f"{system} {WRITER_RULES}"
    reply = llm.chat_json(system, payload, WRITER_SCHEMA)
    if not isinstance(reply, dict):
        trace.add(agent, "Model unavailable", "Used the deterministic writer")
        return fallback, "rules", rules_meta
    for attempt in (1, 2):
        lead, use = str(reply.get("lead", "")), [u for u in reply.get("use") or [] if isinstance(u, str)]
        trace.add(agent, "Drafted" if attempt == 1 else "Revised", f"lead: {lead[:120]} · facts: {', '.join(use) or 'none'}", "llm")
        issues = lead_check(lead, allowed_literals, strict)
        if not issues:
            answer, order = compose(lead, use, facts, required)
            added = [k for k in required if k not in use]
            trace.add("Safety & grounding", "Approved", "Lead states no facts; every figure comes from a verified sentence"
                      + (f"; added required: {', '.join(added)}" if added else ""))
            return answer, "llm", dict(lead=lead.strip(), used=order, picked=[k for k in order if k in use], facts=facts)
        trace.add("Safety & grounding", "Rejected" if attempt == 1 else "Rejected again", "; ".join(issues))
        if attempt == 2:
            break
        reply = llm.chat_json(f"{system} Your previous lead was rejected because: {'; '.join(issues)}. Fix it.", payload, WRITER_SCHEMA)
        if not isinstance(reply, dict):
            break
    trace.add("Safety & grounding", "Fell back", "Deterministic text used instead of the model draft")
    return fallback, "rules", rules_meta


# ---------- Specialists ----------
def _describe(spec):
    parts = [spec.get("pathway"), spec.get("region"), spec.get("nationality"),
             {"F": "female", "M": "male"}.get(spec.get("sex")),
             spec.get("age_min") is not None and f"aged {spec['age_min']:g}+",
             spec.get("age_max") is not None and f"under {spec['age_max']:g}",
             {True: "in this week's plan", False: "not in this week's plan"}.get(spec.get("in_plan"))]
    return ", ".join(p for p in parts if p) or "everyone with a care gap"


def population_query(engine, question, trace):
    rules_spec, _ = parse_rules(question)
    trace.add("Query planner", "Parsed with rules", json.dumps({k: v for k, v in rules_spec.items() if v is not None}) or "{}")
    spec = dict(rules_spec)
    proposal = llm.chat_json(PLANNER_SYS, question, SPEC_SCHEMA)
    if proposal is None:
        trace.add("Query planner", "Model unavailable", "Rule-based filters only")
    else:
        trace.add("Query planner", "Proposed filters", json.dumps({k: v for k, v in proposal.items() if v is not None}), "llm")
        kept, dropped = [], []
        for f in SPEC_FIELDS:
            v = proposal.get(f)
            if v is None or spec.get(f) is not None:
                continue
            allowed = SPEC_SCHEMA["properties"][f].get("enum")
            ok = (v in allowed if allowed else isinstance(v, (int, float)) and not isinstance(v, bool)) and _evidence(f, v, question)
            (kept if ok else dropped).append(f"{f}={v}")
            if ok:
                spec[f] = v
        trace.add("Verifier", "Checked proposal against the question",
                  f"kept {', '.join(kept) or 'nothing new'}; dropped {', '.join(dropped) or 'nothing'}")
    result = engine.query(spec)
    trace.add("Analyst tool", "engine.query()", f"{result['count'] if result['count'] is not None else '<10'} matches")
    filters, count = _describe(spec), result["count"]
    few = lambda n: "fewer than 10" if n is None else f"{n:,}"  # noqa: E731
    facts = {"matches": sentence(f"{count:,} people with a care gap match ({filters})" if count is not None
                                 else f"fewer than 10 people match ({filters}), so the count is suppressed for privacy")}
    if count is not None and spec.get("in_plan") is None:
        facts["in_plan"] = sentence(f"{few(result['in_plan'])} of them are in this week's plan")
    if result["groups"]:
        facts["groups"] = sentence("the largest groups are " + ", ".join(f"{g['group']} ({few(g['count'])})" for g in result["groups"][:3]))
    answer, source, meta = write_checked(trace, "Narrator", NARRATOR_SYS, facts, dict(question=question),
                                   required=["matches"], allowed_literals=filters)
    return answer, source, dict(kind="population", spec=result["spec"], result=result, **meta)


def outreach_check(view):
    msgs = view["messages"]
    leaks = [m["to"] for m in msgs if re.search(r"BRCA|\bFH\b", m["text"])]
    return (f"{len(msgs)} message(s): {sum(not m['private'] for m in msgs)} family, {sum(m['private'] for m in msgs)} private; "
            f"approved templates only; genomic terms {'FOUND for ' + ', '.join(leaks) if leaks else 'absent'}"), not leaks


def case_brief(engine, pid, trace):
    view = engine.person_view(pid)
    p, r = view["person"], view["risk"]
    trace.add("Case reviewer", "Loaded case", f"{p['name']}, {len(view['gaps'])} care gap(s), {len(view['messages'])} message(s)")
    detail, ok = outreach_check(view)
    trace.add("Outreach checker", "Verified household messages", detail)
    withheld = [d["category"] for d in view["provenance"] if not d["authorised"]]
    age = f"{int(p['age'] * 12)} months" if p["age"] < 2 else f"{int(p['age'])} y"
    gaps = "; ".join(f"{g['pathway']} ({g['reason']})" for g in view["gaps"])
    facts = {"summary": sentence(f"{p['name']} ({age}) has care gaps for {gaps}" if gaps else f"{p['name']} ({age}) has no open care gaps")}
    if r:
        facts["risk"] = sentence(f"the modelled 24-month risk of a positive screen is {r['p'] * 100:.1f}% "
                                 f"(80% interval {r['lo'] * 100:.1f}–{r['hi'] * 100:.1f}%)")
        if r["factors"]:
            facts["factors"] = sentence("the risk is raised mainly by " + ", ".join(f["factor"] for f in r["factors"]))
    else:
        facts["risk"] = sentence("children are prioritised by care-gap rules only, with no model score")
    facts["lift"] = sentence(f"an invitation raises the chance of attending by {view['lift'] * 100:.1f} points")
    facts["slot"] = sentence(f"a slot is booked for {view['slot']}" if view["slot"] else "they are not in this week's plan")
    facts["consent"] = sentence(f"withheld by consent: {', '.join(withheld)}" if withheld else "all data categories are authorised")
    answer, source, meta = write_checked(trace, "Case reviewer", BRIEF_SYS, facts, dict(role="clinician brief", outreach_ok=ok),
                                   required=["summary", "risk"], allowed_literals=p["centre"], strict=True)
    return answer, source, dict(kind="case", person_id=pid, **meta)


def safeguards_report(engine, question, trace):
    dim = parse_rules(question)[0]["group_by"] or "region × nationality"
    table = safeguards.segment_table(engine, dim)
    review = [r for r in table["rows"] if r["gate"] == "review"]
    trace.add("Safeguards reporter", "safeguards.segment_table()", f"{dim}: {len(review)} of {len(table['rows'])} segments below the gate")
    below = [f"{r['segment']} ({round(r['reach'] * 100, 1)}%)" for r in review]
    facts = {"reach": sentence(f"overall reach this week is {table['overall_reach'] * 100:.1f}% of people with a care gap"),
             "gate": sentence(f"the release gate is {table['gate'] * table['overall_reach'] * 100:.1f}% (80% of overall reach)"),
             "below": sentence(f"{len(review)} of {len(table['rows'])} {dim} segments fall below the gate and need an outreach "
                               f"redesign: {', '.join(below)}" if review else f"every {dim} segment passes the gate")}
    answer, source, meta = write_checked(trace, "Safeguards reporter", SAFEGUARDS_SYS, facts, dict(question=question), required=["below"])
    return answer, source, dict(kind="safeguards", dimension=dim, below_gate=below, **meta)


# ---------- Supervisor ----------
def route(question, trace):
    spec, pid = parse_rules(question)
    if pid is not None:
        trace.add("Supervisor", "Routed by rules", f"case_brief (person {pid})")
        return "case_brief", pid
    if SAFEGUARD_HINT.search(question.lower()):
        trace.add("Supervisor", "Routed by rules", "safeguards")
        return "safeguards", None
    proposal = llm.chat_json(SUPERVISOR_SYS, question, INTENT_SCHEMA)
    intent = proposal.get("intent") if proposal else None
    if intent == "case_brief":  # no person id in the question: a brief cannot be grounded
        trace.add("Supervisor", "Overrode model", "case_brief needs a person id; using population_query", "llm")
        return "population_query", None
    if intent in INTENTS:
        trace.add("Supervisor", "Routed by model", intent, "llm")
        return intent, None
    trace.add("Supervisor", "Routed by rules", "population_query (default)")
    return "population_query", None


def ask(engine, question):
    trace = Trace()
    intent, pid = route(question, trace)
    if intent == "case_brief" and not 0 <= pid < len(engine.people):
        trace.add("Supervisor", "Stopped", f"no person {pid}")
        return dict(answer=f"There is no person {pid} in this population.", intent=intent, source="rules",
                    trace=trace.steps, facts=None, model=llm.status())
    run = {"case_brief": lambda: case_brief(engine, pid, trace), "safeguards": lambda: safeguards_report(engine, question, trace),
           "population_query": lambda: population_query(engine, question, trace)}[intent]
    answer, source, facts = run()
    return dict(answer=f"{answer.strip()} {DISCLAIMER}", intent=intent, source=source, trace=trace.steps,
                facts=facts, model=llm.status())


def brief(engine, pid):
    trace = Trace()
    trace.add("Supervisor", "Routed by request", f"case_brief (person {pid})")
    answer, source, facts = case_brief(engine, pid, trace)
    return dict(answer=f"{answer.strip()} {DISCLAIMER}", intent="case_brief", source=source, trace=trace.steps,
                facts=facts, model=llm.status())
