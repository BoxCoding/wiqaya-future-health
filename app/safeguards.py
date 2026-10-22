"""Segment checks before and after each release: reach, high-risk reach, calibration, with
small-cell suppression so no published cell can identify a person."""
from collections import defaultdict

MIN_CELL = 10
REACH_GATE = 0.8   # a segment's due-and-contacted rate must be >= 80% of the overall rate
HIGH_RISK = 0.15   # synthetic ground truth; only measurable because the data is simulated


def age_band(a):
    return "0–5" if a < 6 else "6–17" if a < 18 else "18–39" if a < 40 else "40–64" if a < 65 else "65+"


DIMENSIONS = {
    "nationality": lambda p: p["nationality"],
    "region": lambda p: p["region"],
    "sex": lambda p: p["sex"],
    "language": lambda p: p["language"],
    "age band": lambda p: age_band(p["age"]),
    "region × nationality": lambda p: f"{p['region']} · {p['nationality']}",
}


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _row(engine, segment, members, overall):
    contacted = engine.contacted
    due = [p for p in members if p["id"] in engine.priority]
    high = [p for p in due if p["_truth"]["risk"] >= HIGH_RISK]
    screened = [p for p in members if p["age"] >= 18 and p["_truth"]["screened_hist"]]
    reach = _mean([p["id"] in contacted for p in due])
    return dict(
        segment=segment, n=len(members), due=len(due), contacted=sum(p["id"] in contacted for p in due),
        reach=reach, gate=None if reach is None else ("pass" if reach >= REACH_GATE * overall else "review"),
        high_risk_reach=_mean([p["id"] in contacted for p in high]),
        predicted_risk=_mean([engine.risk[p["id"]][0] for p in screened]),
        observed_rate=_mean([p["_truth"]["positive_hist"] for p in screened]),
        suppressed=False,
    )


def suppress(rows):
    """Hide cells under MIN_CELL; if only one is hidden, hide the next smallest too so totals can't reveal it."""
    small = [r for r in rows if r["n"] < MIN_CELL or r["due"] < MIN_CELL]
    if len(small) == 1 and len(rows) > 2:
        small.append(min((r for r in rows if r is not small[0]), key=lambda r: r["n"]))
    for r in small:
        r.update({k: None for k in r if k not in ("segment", "suppressed")}, suppressed=True)
    return rows


def segment_table(engine, dimension):
    key = DIMENSIONS[dimension]
    groups = defaultdict(list)
    for p in engine.people:
        groups[key(p)].append(p)
    overall = _mean([p["id"] in engine.contacted for p in engine.people if p["id"] in engine.priority]) or 0.0
    rows = [_row(engine, seg, members, overall) for seg, members in sorted(groups.items())]
    return dict(dimension=dimension, overall_reach=overall, gate=REACH_GATE, min_cell=MIN_CELL, rows=suppress(rows))
