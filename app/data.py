"""Synthetic Abu Dhabi-like population. Every person is invented; no real records are used.

Each person carries a hidden `_truth` block (true risk, attendance with and without an
invitation, past history). Models only see observed fields; `_truth` exists so the demo can
score policies against ground truth, which is impossible with real data.
"""
import numpy as np

REGIONS = {"Abu Dhabi City": 0.6, "Al Ain": 0.3, "Al Dhafra": 0.1}
CENTRES_PER_REGION = {"Abu Dhabi City": 4, "Al Ain": 3, "Al Dhafra": 1}
NATIONALITIES = {"Emirati": 0.35, "Arab expatriate": 0.2, "South Asian": 0.25,
                 "Filipino": 0.08, "Western": 0.05, "Other": 0.07}
# Outreach is in Arabic or English; names follow the household's background, not its language.
LANGUAGES = {"Emirati": "ar", "Arab expatriate": "ar", "South Asian": "en", "Filipino": "en", "Western": "en", "Other": "en"}
_ARAB = {"F": ["Fatima", "Mariam", "Aisha", "Noura", "Hessa", "Salama"],
         "M": ["Ahmed", "Mohammed", "Khalid", "Omar", "Saeed", "Rashid"]}
NAMES = {
    "Emirati": _ARAB, "Arab expatriate": _ARAB,
    "South Asian": {"F": ["Ayesha", "Priya", "Lakshmi", "Sana", "Meera", "Zainab"],
                    "M": ["Ali", "Rajesh", "Bilal", "Joseph", "Usman", "Anil"]},
    "Filipino": {"F": ["Maria", "Joy", "Grace", "Liza"], "M": ["Jose", "Mark", "Paolo", "Carlo"]},
    "Western": {"F": ["Emma", "Sophie", "Chloe", "Grace"], "M": ["James", "Oliver", "Daniel", "Lucas"]},
    "Other": {"F": ["Elena", "Amara", "Nadia", "Sofia"], "M": ["Daniel", "Kofi", "Ivan", "Mateo"]},
}


def sigmoid(z):
    return 1 / (1 + np.exp(-z))


def _pick(rng, weights):
    keys = list(weights)
    return keys[rng.choice(len(keys), p=list(weights.values()))]


def build(n_households=12000, seed=7):
    """Returns (people, centres). A person's `id` equals its index in `people`."""
    rng = np.random.default_rng(seed)
    centres = [f"{r} PHC {i + 1}" for r, k in CENTRES_PER_REGION.items() for i in range(k)]
    people = []
    for hh in range(n_households):
        region, nat = _pick(rng, REGIONS), _pick(rng, NATIONALITIES)
        home = dict(
            household=hh, region=region, nationality=nat,
            language=LANGUAGES[nat],
            centre=str(rng.choice([c for c in centres if c.startswith(region)])),
            distance_km=round(float(rng.gamma(2.0, 2.5 if region == "Abu Dhabi City" else 7.0)), 1),
        )
        # Household-level latent tendency to attend preventive care; never shown to models.
        prop = float(sigmoid(1.2 - 0.09 * home["distance_km"] + rng.normal(0, 0.9)))
        roles = (["guardian"] + ["adult"] * int(rng.random() < 0.75) + ["elder"] * int(rng.random() < 0.15)
                 + ["child"] * int(rng.choice(5, p=[0.3, 0.2, 0.25, 0.15, 0.1])))
        taken = set()
        for role in roles:
            people.append(_person(rng, len(people), role, home, prop, taken))
    return people, centres


def _person(rng, pid, role, home, prop, taken):
    sex = "F" if rng.random() < 0.5 else "M"
    age = {"child": rng.uniform(0, 18), "elder": rng.uniform(60, 82)}.get(role, rng.uniform(24, 58))
    adult = age >= 18
    bmi = float(np.clip(rng.normal(28 if adult else 18, 5 if adult else 3), 14, 50))
    prediabetes = adult and rng.random() < 0.08 + 0.004 * age
    fam_crc_age = int(rng.integers(38, 75)) if adult and rng.random() < 0.08 else None
    variant = ("BRCA1/2" if rng.random() < 0.5 else "FH") if adult and rng.random() < 0.006 else None
    dev_concern = not adult and age < 6 and rng.random() < 0.06
    free = [n for n in NAMES[home["nationality"]][sex] if n not in taken] or NAMES[home["nationality"]][sex]
    name = str(rng.choice(free))
    taken.add(name)

    def years_since_test():  # None = never tested
        return round(float(rng.uniform(0, 5)), 1) if rng.random() < prop else None

    # Under-served households carry more true risk: the pattern a naive model hides.
    risk = (float(sigmoid(-3.6 + 0.045 * (age - 45) + 0.07 * (bmi - 27) + 0.8 * prediabetes
                          + 0.9 * (fam_crc_age is not None) + 1.4 * (variant is not None) + 1.0 * (1 - prop)))
            if adult else 0.05 + 0.4 * dev_concern)
    p0 = 0.7 * prop                      # attends without an invitation
    p1 = p0 + 1.2 * prop * (1 - prop)    # attends with one; lift peaks for the "persuadable" middle
    screened = bool(rng.random() < prop)
    invited = bool(rng.random() < 0.5)   # past outreach was randomised, which makes lift learnable

    return dict(
        id=pid, role=role, name=name, sex=sex,
        age=round(float(age), 1), **home,
        past_attendance=float(rng.binomial(5, prop) / 5), bmi=round(bmi, 1),
        prediabetes=bool(prediabetes), fam_crc_age=fam_crc_age, variant=variant, dev_concern=bool(dev_concern),
        months_since_wcv=None if adult else round(float(rng.exponential(4 if prop > 0.5 else 14)), 1),
        dev_screened=bool(rng.random() < prop),
        years_since={k: years_since_test() for k in ("crc", "cervical", "breast", "hba1c")} if adult else {},
        consent=dict(clinical=True, genomic=bool(rng.random() < 0.85), family_history=bool(rng.random() < 0.95),
                     context=bool(rng.random() < 0.9), outreach=bool(rng.random() < 0.96)),
        family_opt_in=bool(rng.random() < 0.6),
        own_contact=bool(adult or (age >= 14 and rng.random() < 0.7)),
        _truth=dict(risk=risk, p0=p0, p1=p1, screened_hist=screened,
                    positive_hist=bool(screened and rng.random() < risk),
                    invited_hist=invited, attended_hist=bool(rng.random() < (p1 if invited else p0))),
    )
