"""Builds the population once, then derives every flag, score, plan and evaluation from it."""
from collections import Counter, defaultdict

import numpy as np

from . import data, model, outreach, rules
from .safeguards import DIMENSIONS, MIN_CELL

SLOTS_PER_WEEK = 150  # per centre; invitations are paced to real capacity


def provenance(p):
    c = p["consent"]
    return [
        dict(category="Clinical record (age, BMI, diagnoses)", authorised=True, used_for="Care-gap rules, risk model"),
        dict(category="Visit and screening history", authorised=True, used_for="Care-gap rules, risk model, invitation lift"),
        dict(category="Family history", authorised=c["family_history"], used_for="Earlier screening start, risk model"),
        dict(category="Genomic findings", authorised=c["genomic"],
             used_for="Genetic counselling pathway, risk model (actionable finding only)"),
        dict(category="Area context (distance to clinic)", authorised=c["context"],
             used_for="Invitation lift only, never eligibility"),
        dict(category="Outreach messages", authorised=c["outreach"], used_for="Invitations and recalls"),
    ]


# fields each consent flag guards; withheld ones never leave the server
CONSENT_FIELDS = {"family_history": ("fam_crc_age",), "genomic": ("variant",), "context": ("distance_km",)}


def public_person(p):
    hidden = {f for c, fs in CONSENT_FIELDS.items() if not p["consent"][c] for f in fs}
    return {k: v for k, v in p.items() if not k.startswith("_") and k not in hidden}


class Engine:
    def __init__(self, n_households=12000, seed=7, slots=SLOTS_PER_WEEK):
        rng = np.random.default_rng(seed + 1)
        self.people, self.centres = data.build(n_households, seed)
        self.slots = slots
        self.households = defaultdict(list)
        for p in self.people:
            self.households[p["household"]].append(p)

        self.adults = [p for p in self.people if p["age"] >= 18]
        X = np.array([model.features(p) for p in self.adults])
        self.risk_model = model.RiskModel(self.adults, rng)
        self.naive_model = model.RiskModel(self.adults, rng, screened_only=False, n_boot=0)
        point, lo, hi = self.risk_model.predict(X)
        self.risk = {p["id"]: (float(a), float(b), float(c)) for p, a, b, c in zip(self.adults, point, lo, hi)}
        self.lift = dict(zip((p["id"] for p in self.people), map(float, model.LiftModel(self.people).predict(self.people))))

        self.gaps = {p["id"]: rules.care_gaps(p) for p in self.people}
        self.priority = {p["id"]: self._priority(p) for p in self.people if self.gaps[p["id"]]}
        self.plans = {c: self.wiqaya_plan(c) for c in self.centres}
        self.slot_of = {m["id"]: m["slot"] for plan in self.plans.values() for h in plan for m in h["members"]}
        self.contacted = set(self.slot_of)
        self.decisions = []  # shortcut: in-memory, lost on restart; store in PostgreSQL for the pilot

    def need(self, p):
        """Adults: modelled risk. Children: rules only in this prototype (no paediatric model yet)."""
        return self.risk[p["id"]][0] if p["age"] >= 18 else 0.05 + 0.4 * p["dev_concern"]

    def _priority(self, p):
        """Expected early findings an invitation causes. Being overdue makes someone eligible (a care gap);
        it is not multiplied in again, because that shifted slots away from where invites help most."""
        return self.need(p) * self.lift[p["id"]]

    def due(self, centre):
        return [p for p in self.people
                if p["centre"] == centre and p["id"] in self.priority and p["consent"]["outreach"]]

    def wiqaya_plan(self, centre):
        """Fill the week's slots with whole households, best priority per slot first."""
        by_household = defaultdict(list)
        for p in self.due(centre):
            by_household[p["household"]].append(p["id"])
        ranked = sorted(by_household.items(), key=lambda kv: -sum(self.priority[i] for i in kv[1]) / len(kv[1]))
        plan, used = [], 0
        for hh, ids in ranked:
            if used + len(ids) > self.slots:
                continue
            plan.append(dict(household=hh, score=round(sum(self.priority[i] for i in ids) / len(ids), 4),
                             members=[dict(id=i, slot=outreach.slot_time(used + k)) for k, i in enumerate(ids)]))
            used += len(ids)
        return plan

    def calendar_plan(self, centre):
        """Comparison policy: rule-based eligibility, most overdue first, one person per slot."""
        due = sorted(self.due(centre), key=lambda p: -max(g["overdue_days"] for g in self.gaps[p["id"]]))
        return [p["id"] for p in due[: self.slots]]

    def _score(self, ids):
        t = [self.people[i]["_truth"] for i in ids]
        return dict(invited=len(t), completions=round(sum(x["p1"] for x in t)),
                    caused_by_invite=round(sum(x["p1"] - x["p0"] for x in t)),
                    early_findings_caused=round(sum(x["risk"] * (x["p1"] - x["p0"]) for x in t), 1),
                    high_risk_invited=sum(x["risk"] >= 0.15 for x in t))

    def bias_check(self):
        never = [p for p in self.adults if not p["_truth"]["screened_hist"]]
        X = np.array([model.features(p) for p in never])
        return dict(group="Adults never screened", n=len(never),
                    true_risk=round(float(np.mean([p["_truth"]["risk"] for p in never])), 4),
                    corrected_model=round(float(self.risk_model.predict(X)[0].mean()), 4),
                    naive_model=round(float(self.naive_model.predict(X)[0].mean()), 4))

    def outcomes(self):
        wiqaya = list(self.contacted)
        calendar = [i for c in self.centres for i in self.calendar_plan(c)]
        return dict(policies={"Wiqaya: benefit ranking": self._score(wiqaya),
                              "Calendar rule: most overdue first": self._score(calendar)},
                    bias_check=self.bias_check(), calibration=self.risk_model.calibration,
                    slots_per_centre=self.slots, centres=len(self.centres))

    def overview(self):
        due = [p for p in self.people if p["id"] in self.priority]
        return dict(people=len(self.people), households=len(self.households), adults=len(self.adults),
                    children=len(self.people) - len(self.adults), due=len(due),
                    opted_out=sum(not p["consent"]["outreach"] for p in due),
                    by_pathway=Counter(g["pathway"] for p in due for g in self.gaps[p["id"]]).most_common(),
                    centres=self.centres, slots_per_centre=self.slots, week_start=str(outreach.WEEK_START))

    def plan_view(self, centre):
        rows = []
        for h in self.plans[centre]:
            members = []
            for m in h["members"]:
                p = self.people[m["id"]]
                members.append(dict(id=p["id"], name=p["name"], age=p["age"], role=p["role"], slot=m["slot"],
                                    pathways=[g["pathway"] for g in self.gaps[p["id"]]],
                                    priority=round(self.priority[p["id"]], 4)))
            rows.append(dict(household=h["household"], score=h["score"],
                             language=self.people[h["members"][0]["id"]]["language"], members=members))
        return dict(centre=centre, slots=self.slots, used=sum(len(h["members"]) for h in rows), households=rows)

    def person_view(self, pid):
        p = self.people[pid]
        risk = self.risk.get(pid)
        household = self.households[p["household"]]
        return dict(
            person=public_person(p),
            gaps=self.gaps[pid], priority=self.priority.get(pid), lift=round(self.lift[pid], 4),
            slot=self.slot_of.get(pid),
            risk=None if risk is None else dict(p=risk[0], lo=risk[1], hi=risk[2], interval="80%",
                                                factors=self.risk_model.explain(model.features(p))),
            provenance=provenance(p),
            household=[dict(id=m["id"], name=m["name"], age=m["age"], role=m["role"], due=bool(self.gaps[m["id"]]))
                       for m in household],
            messages=outreach.compose(household, self.gaps, self.slot_of),
            decisions=[d for d in self.decisions if d["person_id"] == pid],
        )

    def query(self, spec):
        """The analyst agent's tool: people with a care gap matching `spec`, counts small-cell suppressed."""
        def keep(p):
            gaps = self.gaps[p["id"]]
            return bool(gaps) and all((
                spec.get("region") in (None, p["region"]), spec.get("nationality") in (None, p["nationality"]),
                spec.get("sex") in (None, p["sex"]), spec.get("centre") in (None, p["centre"]),
                spec.get("age_min") is None or p["age"] >= spec["age_min"],
                spec.get("age_max") is None or p["age"] < spec["age_max"],
                spec.get("pathway") is None or any(g["pathway"] == spec["pathway"] for g in gaps),
                spec.get("in_plan") is None or (p["id"] in self.contacted) == spec["in_plan"]))
        hits = [p for p in self.people if keep(p)]
        hide = lambda n: n if n >= MIN_CELL else None  # noqa: E731
        groups = []
        if spec.get("group_by") in DIMENSIONS:
            counts = Counter(DIMENSIONS[spec["group_by"]](p) for p in hits)
            groups = [{"group": g, "count": hide(n)} for g, n in counts.most_common()]
        top = sorted(hits, key=lambda p: -self.priority[p["id"]])[:5] if len(hits) >= MIN_CELL else []
        return dict(spec={k: v for k, v in spec.items() if v is not None}, count=hide(len(hits)),
                    in_plan=hide(sum(p["id"] in self.contacted for p in hits)), groups=groups,
                    top=[dict(id=p["id"], name=p["name"], age=p["age"], centre=p["centre"],
                              benefit_per_100=round(self.priority[p["id"]] * 100, 1)) for p in top])
