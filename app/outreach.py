"""Approved message templates and household message composition. No LLM: slots are filled in code,
so every sent message matches an approved template word for word.

Privacy rules: an adult's own screening appears in the shared family message only if that adult
opted in; genomic findings are never named in any message; adolescents with their own contact
also get a private message offering a confidential part of the visit.
shortcut: translations are prototype drafts; native-speaker and clinical review before use.
"""
from datetime import date, timedelta

TEMPLATES = {
    "en": dict(teen="{name}, part of your health check can be just you and the doctor, in confidence. Reply 2 to ask a question privately.",
               hello="Hello {name},", child="{child} is due a Well-Child Visit.",
               adult="{name}, you are due a preventive health check.",
               genomic="{name}, please book a conversation with our genetic counsellor about your results.",
               slot="Suggested time: {when} at {centre}.", soon="We will offer you a time shortly.",
               actions="Reply 1 to book, 2 to ask a question, 3 to remind me later, 0 to opt out."),
    "ar": dict(teen="{name}، يمكن أن يكون جزء من فحصك الصحي بينك وبين الطبيب فقط وبسرية تامة. أرسل 2 لطرح سؤال بشكل خاص.",
               hello="مرحباً {name}،", child="حان موعد زيارة الطفل السليم لـ {child}.",
               adult="{name}، حان موعد فحصك الصحي الوقائي.",
               genomic="{name}، يرجى حجز موعد مع مستشار الوراثة لمناقشة نتائجك.",
               slot="الموعد المقترح: {when} في {centre}.", soon="سنعرض عليك موعداً قريباً.",
               actions="أرسل 1 للحجز، 2 لطرح سؤال، 3 للتذكير لاحقاً، 0 لإلغاء الاشتراك."),
}

WEEK_START = date(2026, 10, 26)  # a Monday; slots run Monday to Friday
# 20-minute slots 08:00–15:40, skipping 12:00–13:00 so invitations avoid the midday prayer window.
TIMES = [f"{h:02d}:{m:02d}" for h in range(8, 16) if h != 12 for m in (0, 20, 40)]
ROOMS = 2
ADOLESCENT_AGE = 14  # shortcut: placeholder; local policy sets the age for confidential adolescent care


def slot_time(i):
    """The i-th slot of the week; household members get consecutive (back-to-back) slots."""
    s = i // ROOMS
    day, t = divmod(s, len(TIMES))
    return f"{(WEEK_START + timedelta(days=day)).strftime('%a %d %b')} {TIMES[t]}"


def _channel(p):
    return "Nurse phone call" if p["age"] >= 65 else "SMS + app"


def compose(household, gaps, slot_of):
    """household: person dicts; gaps: id -> gap list; slot_of: id -> slot text for this week's plan."""
    due = [p for p in household if gaps[p["id"]] and p["consent"]["outreach"]]
    guardian = next((p for p in household if p["role"] == "guardian"), None)
    if not due or guardian is None:
        return []
    t = TEMPLATES[guardian["language"]]

    def with_slot(line, p):
        when = slot_of.get(p["id"])
        return f"{line} " + (t["slot"].format(when=when, centre=p["centre"]) if when else t["soon"])

    def message(to, lines, private):
        return dict(to=to["name"], to_id=to["id"], language=to["language"], channel=_channel(to), private=private,
                    text="\n".join([t["hello"].format(name=to["name"]), *lines, t["actions"]]))

    family, private = [], []
    for p in due:
        genomic = any(g["pathway"].startswith("Genetic") for g in gaps[p["id"]])
        routine = any(not g["pathway"].startswith("Genetic") for g in gaps[p["id"]])
        if p["age"] < 18:
            family.append(with_slot(t["child"].format(child=p["name"]), p))
            if p["age"] >= ADOLESCENT_AGE and p["own_contact"]:
                private.append(message(p, [t["teen"].format(name=p["name"])], True))
            continue
        if genomic:
            private.append(message(p, [with_slot(t["genomic"].format(name=p["name"]), p)], True))
        if routine:
            line = with_slot(t["adult"].format(name=p["name"]), p)
            if p["id"] == guardian["id"] or p["family_opt_in"]:
                family.append(line)
            else:
                private.append(message(p, [line], True))
    return ([message(guardian, family, False)] if family else []) + private
