"""Deterministic care-gap rules: a visit or test is overdue. Separate from the risk score.

shortcut: intervals and start ages are PLACEHOLDERS, not the DoH-approved Abu Dhabi schedules;
replace with the clinical leads' versioned catalogue before any pilot.
"""

GUIDELINE = "Placeholder schedule (replace with DoH-approved guideline)"
GENOMIC_PATHWAY = "Genetic counselling, then enhanced surveillance"
PATHWAYS = ["Well-Child Visit", "Well-Child Visit + developmental assessment", "Adult screening: colorectal",
            "Adult screening: cervical", "Adult screening: breast", "Adult screening: diabetes (HbA1c)", GENOMIC_PATHWAY]


def wcv_interval_months(age):
    return 2 if age < 1 else 6 if age < 3 else 12


def _gap(pathway, reason, overdue_days, data):
    return dict(pathway=pathway, reason=reason, overdue_days=int(max(overdue_days, 0)),
                data=data, guideline=GUIDELINE)


def _screen(p, key, start, every, pathway, extra_data=()):
    """Gap if the last test is older than `every` years, or there was never one since `start` age."""
    since = p["years_since"][key]
    if since is not None and since <= every:
        return None
    overdue = (since - every) * 365 if since is not None else (p["age"] - start) * 365
    reason = f"Last test {since:.1f} years ago; due every {every}" if since is not None else "No test on record"
    return _gap(pathway, reason, overdue, ["age", "screening history", *extra_data])


def care_gaps(p):
    a = p["age"]
    if a < 18:
        gaps = []
        due, since = wcv_interval_months(a), p["months_since_wcv"]
        if since > due:
            gaps.append(_gap("Well-Child Visit", f"Last visit {since:.0f} months ago; due every {due}",
                             (since - due) * 30.4, ["age", "visit history"]))
        if 0.75 <= a <= 3 and (p["dev_concern"] or not p["dev_screened"]):
            reason = "Developmental concern recorded" if p["dev_concern"] else "No developmental screen at 9–36 months"
            gaps.append(_gap("Well-Child Visit + developmental assessment", reason,
                             30 if p["dev_concern"] else (a - 0.75) * 365, ["age", "developmental screening"]))
        return gaps

    c, female = p["consent"], p["sex"] == "F"
    found = []
    crc_start, crc_data = 45, ()
    if c["family_history"] and p["fam_crc_age"]:
        crc_start, crc_data = min(40, p["fam_crc_age"] - 10), ("family history",)
    if crc_start <= a <= 75:
        found.append(_screen(p, "crc", crc_start, 2, "Adult screening: colorectal", crc_data))
    if female and 25 <= a <= 65:
        found.append(_screen(p, "cervical", 25, 3, "Adult screening: cervical"))
    if female and 40 <= a <= 69:
        found.append(_screen(p, "breast", 40, 2, "Adult screening: breast"))
    if p["prediabetes"]:
        found.append(_screen(p, "hba1c", 18, 1, "Adult screening: diabetes (HbA1c)", ("diagnoses",)))
    elif a >= 35:
        found.append(_screen(p, "hba1c", 35, 3, "Adult screening: diabetes (HbA1c)"))
    if p["variant"] and c["genomic"]:
        found.append(_gap(GENOMIC_PATHWAY, f"Actionable genomic finding on record ({p['variant']})", 30,
                          ["genomic findings"]))
    return [g for g in found if g]
