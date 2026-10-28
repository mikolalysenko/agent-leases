"""SQLite is the authority. Reserve before dispatch; never refund uncertainty."""

from contextlib import contextmanager
import getpass
import hashlib
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote
import uuid

from .policy import (Denied, LIMITS, canonical, compile_policy, digest,
                     fields, integer, parse_json, string, validate_request)
from .adapters import binding


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.database = self.directory / "lease.sqlite3"

    @classmethod
    def create(cls, directory, policy):
        policy = compile_policy(policy)
        store = cls(directory)
        store.directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        with sqlite3.connect(store.database) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE lease (id TEXT PRIMARY KEY, policy TEXT NOT NULL,
                    approved REAL NOT NULL, expires REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0, used TEXT NOT NULL);
                CREATE TABLE requests (id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                    operation TEXT NOT NULL, state TEXT NOT NULL, response TEXT);
                CREATE TABLE owned (kind TEXT NOT NULL, resource TEXT NOT NULL,
                    PRIMARY KEY (kind, resource));
                CREATE TABLE events (sequence INTEGER PRIMARY KEY, record TEXT NOT NULL,
                    previous TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE inbox (id TEXT PRIMARY KEY, created REAL NOT NULL,
                    body TEXT NOT NULL, state TEXT NOT NULL, answer TEXT);
            """)
            now = time.time()
            used = {key: 0 for key in tuple(policy["capabilities"]) + LIMITS}
            lease_id = uuid.uuid4().hex
            db.execute("INSERT INTO lease VALUES (?, ?, ?, ?, 0, ?)",
                       (lease_id, canonical(policy), now, now + policy["ttl_seconds"],
                        canonical(used)))
            store._event(db, "approved", policy=policy, policy_hash=digest(policy),
                         principal=getpass.getuser(), lease=lease_id)
        store.database.chmod(0o600)
        return store

    @contextmanager
    def transaction(self):
        # mode=rw prevents a typo or missing authority from creating a fresh DB.
        db = sqlite3.connect("file:" + quote(str(self.database)) + "?mode=rw",
                             uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, kind, **details):
        row = db.execute("SELECT sequence, hash FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
        sequence, previous = (row[0] + 1, row[1]) if row else (1, "0" * 64)
        record = canonical({"sequence": sequence, "time": time.time(), "event": kind, **details})
        event_hash = hashlib.sha256((previous + record).encode()).hexdigest()
        db.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                   (sequence, record, previous, event_hash))

    def _lease(self, db):
        row = dict(db.execute("SELECT * FROM lease").fetchone())
        row["policy"] = parse_json(row["policy"])
        row["used"] = parse_json(row["used"])
        return row

    def _active(self, lease):
        if lease["revoked"]:
            raise Denied("revoked", "lease is revoked")
        if time.time() >= lease["expires"]:
            raise Denied("expired", "lease has expired")

    def status(self):
        with self.transaction() as db:
            lease = self._lease(db)
            caps = {**lease["policy"]["capabilities"], **lease["policy"]["limits"]}
            lease["left"] = {key: cap - lease["used"][key] for key, cap in caps.items()}
            lease["state"] = ("revoked" if lease["revoked"] else
                              "expired" if time.time() >= lease["expires"] else "active")
            lease["seconds_left"] = max(0, int(lease["expires"] - time.time()))
            return lease

    def reserve(self, request):
        with self.transaction() as db:
            lease = self._lease(db)
            self._active(lease)
            validate_request(request, lease["policy"])
            previous = db.execute("SELECT * FROM requests WHERE id=?", (request["id"],)).fetchone()
            if previous:
                if previous["digest"] != digest(request):
                    raise Denied("conflict", "request id already has different contents")
                if previous["state"] == "pending":
                    raise Denied("in_progress", "request is already in flight; inspect status later")
                return parse_json(previous["response"])
            op, params = request["operation"], request["params"]
            spec = binding(lease["policy"], op)
            owned = spec["kind"].validate(spec["options"], spec["local_name"], params)
            if owned:
                self._owned(db, spec["service"], *owned)
            charges = {op: 1, "operations": 1,
                       "write_operations": int(spec["write"]),
                       "payload_bytes": len(canonical(params).encode())}
            caps = {**lease["policy"]["capabilities"], **lease["policy"]["limits"]}
            for key, amount in charges.items():
                if lease["used"][key] + amount > caps[key]:
                    raise Denied("limit", key + " exhausted", used=lease["used"][key],
                                 left=caps[key] - lease["used"][key], needed=amount,
                                 fix="Ask the operator to grant more " + key)
            for key, amount in charges.items():
                lease["used"][key] += amount
            db.execute("UPDATE lease SET used=?", (canonical(lease["used"]),))
            db.execute("INSERT INTO requests VALUES (?, ?, ?, 'pending', NULL)",
                       (request["id"], digest(request), op))
            self._event(db, "reserved", request=request["id"], operation=op,
                        request_hash=digest(request), charges=charges, used=lease["used"])
        return None

    def _owned(self, db, service, kind, resource):
        kind = service + "." + kind if service else kind
        if not db.execute("SELECT 1 FROM owned WHERE kind=? AND resource=?",
                          (kind, resource)).fetchone():
            raise Denied("scope", "resource was not created by this lease")

    def dispatch(self, request_id, method, path):
        # This transaction is the dispatch authorization's linearization point.
        # Revocation cannot recall a request already authorized here.
        with self.transaction() as db:
            self._active(self._lease(db))
            self._event(db, "dispatch", request=request_id, method=method, path=path)

    def finish(self, request, response, owned=None):
        with self.transaction() as db:
            if owned:
                spec = binding(self._lease(db)["policy"], request["operation"])
                kind, resource = owned
                kind = spec["service"] + "." + kind if spec["service"] else kind
                db.execute("INSERT INTO owned VALUES (?, ?)", (kind, resource))
            db.execute("UPDATE requests SET state='done', response=? WHERE id=?",
                       (canonical(response), request["id"]))
            self._event(db, "completed", request=request["id"], ok=response["ok"],
                        error=response.get("error"), result_hash=digest(response))

    def denial(self, error):
        denial_id = uuid.uuid4().hex
        result = {"ok": False, "error": {"denial": denial_id, **error.data}}
        with self.transaction() as db:
            self._event(db, "denied", **result["error"])
        return result

    def recover(self):
        with self.transaction() as db:
            # Upgrade existing v0.2 state without changing its approved policy.
            db.execute("CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, created REAL NOT NULL, "
                       "body TEXT NOT NULL, state TEXT NOT NULL, answer TEXT)")
            rows = db.execute("SELECT id FROM requests WHERE state='pending'").fetchall()
            for row in rows:
                response = {"ok": False, "error": {"code": "uncertain", "retryable": False,
                            "rule": "gateway stopped during this attempt; inspect the service before new work"}}
                db.execute("UPDATE requests SET state='done', response=? WHERE id=?",
                           (canonical(response), row["id"]))
                self._event(db, "recovered_uncertain", request=row["id"])

    def revoke(self):
        with self.transaction() as db:
            db.execute("UPDATE lease SET revoked=1")
            self._event(db, "revoked", principal=getpass.getuser())

    def grant(self, cap, amount, expected_hash):
        integer(amount, "grant amount", 1)
        with self.transaction() as db:
            self._grant(db, cap, amount, expected_hash)

    def _grant(self, db, cap, amount, expected_hash):
        lease = self._lease(db)
        self._active(lease)
        policy = lease["policy"]
        if digest(policy) != expected_hash:
            raise Denied("conflict", "policy changed since rendering; review again")
        if cap not in policy["capabilities"] and cap not in LIMITS:
            raise Denied("invalid", "unknown cap")
        section = "capabilities" if cap in policy["capabilities"] else "limits"
        before = policy[section][cap]
        policy[section][cap] += amount
        compile_policy(policy)
        db.execute("UPDATE lease SET policy=?", (canonical(policy),))
        self._event(db, "granted", cap=cap, before=before, after=policy[section][cap],
                    principal=getpass.getuser(), policy_hash=digest(policy))

    def submit(self, request):
        """Worker may ask, but cannot supply the answer or change the policy."""
        fields(request, ("id", "kind", "message"), ("cap", "amount"))
        string(request["id"], "request id", maximum=64, pattern=r"[A-Za-z0-9_-]+")
        string(request["message"], "agent's message", maximum=2000)
        if request["kind"] == "more":
            fields(request, ("id", "kind", "message", "cap", "amount"))
            string(request["cap"], "cap", maximum=100)
            integer(request["amount"], "requested amount", 1)
        elif request["kind"] == "ask":
            fields(request, ("id", "kind", "message"))
        else:
            raise Denied("invalid", "request kind must be ask or more")
        with self.transaction() as db:
            lease = self._lease(db)
            self._active(lease)
            previous = db.execute("SELECT * FROM inbox WHERE id=?", (request["id"],)).fetchone()
            if previous:
                if previous["body"] != canonical(request):
                    raise Denied("conflict", "request id already has different contents")
                return self._inbox_row(previous, lease)
            if request["kind"] == "more" and request["cap"] not in {
                    **lease["policy"]["capabilities"], **lease["policy"]["limits"]}:
                raise Denied("scope", "can only request more of an existing cap")
            count = db.execute("SELECT COUNT(*) FROM inbox WHERE created>?", (time.time() - 3600,)).fetchone()[0]
            if count >= 3:
                raise Denied("limit", "at most 3 questions or requests per hour")
            db.execute("INSERT INTO inbox VALUES (?, ?, ?, 'pending', NULL)",
                       (request["id"], time.time(), canonical(request)))
            self._event(db, "asked", request=request["id"], request_kind=request["kind"], body_hash=digest(request))
            return {"id": request["id"], "state": "pending", "request": request, "answer": None}

    def _inbox_row(self, row, lease):
        state = row["state"]
        if state == "pending" and (lease["revoked"] or time.time() >= lease["expires"]):
            state = "closed"
        return {"id": row["id"], "state": state, "request": parse_json(row["body"]),
                "answer": parse_json(row["answer"]) if row["answer"] else None}

    def inbox(self, request_id=None):
        if request_id is not None:
            string(request_id, "request id", maximum=64, pattern=r"[A-Za-z0-9_-]+")
        with self.transaction() as db:
            lease = self._lease(db)
            rows = db.execute("SELECT * FROM inbox ORDER BY created").fetchall()
            result = [self._inbox_row(row, lease) for row in rows if request_id is None or row["id"] == request_id]
            if request_id is not None:
                if not result:
                    raise Denied("scope", "no such request")
                return result[0]
            return result

    def resolve(self, request_id, decision, message, amount, expected_hash):
        """Operator only; answering and granting commit in the same transaction."""
        string(message, "operator answer", minimum=0, maximum=2000)
        if decision not in ("approve", "deny", "answer"):
            raise Denied("invalid", "invalid decision")
        with self.transaction() as db:
            lease = self._lease(db)
            self._active(lease)
            row = db.execute("SELECT * FROM inbox WHERE id=?", (request_id,)).fetchone()
            if not row or row["state"] != "pending":
                raise Denied("conflict", "request is missing or already answered")
            request = parse_json(row["body"])
            granted = 0
            if decision == "approve":
                if request["kind"] != "more":
                    raise Denied("invalid", "a question grants no access; use answer")
                granted = request["amount"] if amount is None else amount
                integer(granted, "approved amount", 1, request["amount"])
                self._grant(db, request["cap"], granted, expected_hash)
            elif decision == "answer" and request["kind"] != "ask":
                raise Denied("invalid", "a budget request requires approve or deny")
            elif amount is not None:
                raise Denied("invalid", "amount only applies to approval")
            answer = {"decision": decision, "message": message, "granted": granted}
            db.execute("UPDATE inbox SET state='answered', answer=? WHERE id=?", (canonical(answer), request_id))
            self._event(db, "answered", request=request_id, decision=decision, granted=granted,
                        principal=getpass.getuser(), answer_hash=digest(answer))

    def events(self):
        with self.transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY sequence")]

    def verify(self):
        previous = "0" * 64
        for sequence, row in enumerate(self.events(), 1):
            expected = hashlib.sha256((previous + row["record"]).encode()).hexdigest()
            if row["sequence"] != sequence or row["previous"] != previous or row["hash"] != expected:
                raise Denied("audit", "audit chain is invalid")
            previous = expected
        return previous
