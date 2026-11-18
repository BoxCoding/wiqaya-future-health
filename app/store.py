"""SQLite persistence: users, a hash-chained audit log, clinician decisions and care-episode snapshots.

The population and the simulated history are rebuilt from the seed at startup; every change made since
(ingest, sign-off, approval, reported barrier, decision) is saved here and replayed on top, so a restart
loses nothing. The audit log is append-only: each row carries the hash of the previous one, so an edited
or deleted row breaks the chain and verify() reports it.

shortcut: one SQLite file per process; a pilot uses a managed PostgreSQL in the UAE with encryption at rest.
"""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, pw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, user TEXT NOT NULL, role TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL, ip TEXT NOT NULL, prev TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, data TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
"""
FIELDS = ("at", "user", "role", "action", "target", "detail", "ip")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(prev, row):
    return hashlib.sha256((prev + json.dumps([row[f] for f in FIELDS], ensure_ascii=False)).encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    # ---------- users ----------
    def user(self, username):
        r = self.db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(r) if r else None

    def add_user(self, username, name, role, pw_hash):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)", (username, name, role, pw_hash))

    def users(self):
        return [dict(r) for r in self.db.execute("SELECT username, name, role FROM users ORDER BY role, username")]

    # ---------- audit ----------
    def audit(self, user, role, action, target="", detail="", ip=""):
        row = dict(at=now(), user=user, role=role, action=action, target=str(target), detail=str(detail)[:500], ip=ip)
        with self.lock, self.db:
            last = self.db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else ""
            self.db.execute("INSERT INTO audit (at, user, role, action, target, detail, ip, prev, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                            (*[row[f] for f in FIELDS], prev, _hash(prev, row)))

    def audits(self, limit=200, action=None):
        q, args = "SELECT * FROM audit", []
        if action:
            q, args = q + " WHERE action LIKE ?", [action + "%"]
        return [dict(r) for r in self.db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    def verify(self):
        """True when every audit row still matches the hash chain."""
        prev = ""
        for r in self.db.execute("SELECT * FROM audit ORDER BY id"):
            r = dict(r)
            if r["prev"] != prev or r["hash"] != _hash(prev, r):
                return False
            prev = r["hash"]
        return True

    # ---------- care episodes and decisions ----------
    def save_episode(self, ep):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO episodes VALUES (?, ?, ?)", (ep["id"], json.dumps(ep, ensure_ascii=False), now()))

    def episodes(self):
        return [json.loads(r["data"]) for r in self.db.execute("SELECT data FROM episodes ORDER BY at")]

    def save_decision(self, record):
        with self.lock, self.db:
            self.db.execute("INSERT INTO decisions (data) VALUES (?)", (json.dumps(record, ensure_ascii=False),))

    def decisions(self):
        return [json.loads(r["data"]) for r in self.db.execute("SELECT data FROM decisions ORDER BY id")]
