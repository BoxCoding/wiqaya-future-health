"""Login, sessions and roles.

Passwords are hashed with scrypt. A session is an HttpOnly, SameSite=Strict cookie signed with HMAC-SHA256;
it carries the user, role and an expiry, so the server keeps no session table. Roles follow least privilege:

  clinician    individual records, AI case briefs, care-gap decisions, clinician sign-off of care episodes
  coordinator  individual records, follow-up worklist, outreach approval, patient-reported barriers
  planner      population views only: no names, no individual records
  admin        everything a planner sees, plus the audit log

Guest access (required by the hackathon) signs in with a chosen role and is labelled "guest" everywhere.
Set WIQAYA_GUEST=0 and WIQAYA_DEMO_USERS=0 outside a demo.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request

ROLES = {
    "clinician": "Clinician",
    "coordinator": "Care coordinator",
    "planner": "Population planner",
    "admin": "Privacy officer",
}
DEMO_PASSWORD = os.getenv("WIQAYA_DEMO_PASSWORD", "Wiqaya-Demo-2026")  # shown on the login page: demo only
DEMO_USERS = [("dr.amna", "Dr. Amna Al Mansoori", "clinician"), ("sara.coord", "Sara Haddad", "coordinator"),
              ("omar.planner", "Omar Rashid", "planner"), ("privacy.admin", "Layla Nasser", "admin")]
# Each staff account works at one primary-care centre; its plan and top-ranked household open first.
HOME_CENTRE = {"dr.amna": "Al Ain PHC 1", "sara.coord": "Abu Dhabi City PHC 2",
               "guest-clinician": "Abu Dhabi City PHC 3", "guest-coordinator": "Al Dhafra PHC 1"}
COOKIE = "wiqaya_session"
TTL_S = 8 * 3600
SECRET = (os.getenv("WIQAYA_SESSION_SECRET") or secrets.token_hex(32)).encode()  # unset: sessions end on restart


def guest_enabled():
    return os.getenv("WIQAYA_GUEST", "1") != "0"


def hash_password(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()}"


def check_password(pw, stored):
    try:
        _, salt, _ = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(pw, bytes.fromhex(salt)), stored)


def seed(store):
    if os.getenv("WIQAYA_DEMO_USERS", "1") != "0":
        for username, name, role in DEMO_USERS:
            if not store.user(username):
                store.add_user(username, name, role, hash_password(DEMO_PASSWORD))


def sign(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return f"{body}.{hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()}"


def unsign(token):
    body, _, mac = (token or "").partition(".")
    if not mac or not hmac.compare_digest(mac, hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    return data if data.get("exp", 0) > time.time() and data.get("role") in ROLES else None


def session_for(username, name, role, guest=False):
    return dict(u=username, name=name, role=role, guest=guest, exp=int(time.time()) + TTL_S)


def current(request: Request):
    """The signed-in user, or 401. Used by every /api route except /health and /api/auth/*."""
    s = unsign(request.cookies.get(COOKIE))
    if not s:
        raise HTTPException(401, "Sign in to continue")
    return s


def require(*roles):
    def dep(request: Request):
        s = current(request)
        if s["role"] not in roles:
            request.app.state.store.audit(s["u"], s["role"], "access.denied", request.url.path, f"needs {'/'.join(roles)}",
                                          client_ip(request))
            raise HTTPException(403, f"Your role ({ROLES[s['role']]}) cannot do this")
        return s
    return dep


def display(s):
    return f"{s['name']} (guest)" if s.get("guest") else s["name"]


def public(s):
    """The signed-in user as the browser sees it."""
    return dict(user=s["u"], name=display(s), role=s["role"], role_label=ROLES[s["role"]], guest=s["guest"],
                centre=HOME_CENTRE.get(s["u"]))


def client_ip(request: Request):
    # Cloud Run's front end appends the caller's address last; anything earlier is client-supplied.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "?")
