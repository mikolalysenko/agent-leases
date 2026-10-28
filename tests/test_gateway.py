from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_leases.gateway import Gateway, Server, client
from agent_leases.policy import Denied, compile_policy, digest, parse_json, render
from agent_leases.store import Store
from tests.fake_github import FakeGitHub


ROOT = Path(__file__).resolve().parents[1]


def policy():
    return json.loads((ROOT / "examples/lease.json").read_text())


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="al-")
        self.store = Store.create(Path(self.temp.name) / "s", policy())
        self.remote = FakeGitHub()
        self.gateway = Gateway(self.store, self.remote)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, op="read_file", params=None, key="read-1", gateway=None):
        return (gateway or self.gateway).handle({"id": key, "operation": op,
                      "params": {"path": "README.md"} if params is None else params})

    def publish(self, key="change-1"):
        return self.call("publish", {"message": "Fix test", "files": {"test.py": "assert True\n"}}, key)

    def pr(self):
        branch = self.publish()["result"]["branch"]
        return self.call("open_pr", {"branch": branch, "title": "Fix test", "body": "Tested"}, "pr-1")

    def test_complete_workflow(self):
        self.assertTrue(self.call()["ok"])
        self.assertEqual(self.pr()["result"]["number"], 7)
        self.assertTrue(self.call("comment", {"number": 7, "body": "Done"}, "comment-1")["ok"])
        status = self.store.status()
        self.assertEqual(status["used"]["operations"], 4)
        self.assertEqual(status["used"]["write_operations"], 3)
        self.assertEqual(len(self.remote.calls), 7)
        refs = [call for call in self.remote.calls if call[3].endswith("/git/refs")]
        self.assertEqual(refs[0][4]["ref"], "refs/heads/lease/%s/change-1" % status["id"])
        pr = next(call[4] for call in self.remote.calls if call[3].endswith("/pulls"))
        self.assertTrue(pr["draft"])
        self.assertFalse(pr["maintainer_can_modify"])
        self.assertIn("Lease: " + status["id"], pr["body"])
        self.assertEqual(len(self.store.verify()), 64)

    def test_read_is_pinned_and_cannot_choose_host_repo_or_ref(self):
        self.call()
        self.assertIn("?ref=" + policy()["base_sha"], self.remote.calls[0][3])
        for extra in ({"repo": "attacker/exfil"}, {"ref": "evil"}, {"token": "foreign"},
                      {"url": "https://attacker.invalid"}):
            result = self.call(params={"path": "README.md", **extra}, key="bad")
            self.assertEqual(result["error"]["code"], "invalid")
        self.assertEqual(len(self.remote.calls), 1)

    def test_no_mutation_escape_hatches(self):
        for op in ("merge", "approve", "graphql", "http", "revoke", "grant", "exec"):
            self.assertEqual(self.call(op, {}, op)["error"]["code"], "unknown_operation")
        self.assertEqual(self.remote.calls, [])

    def test_malformed_and_traversal_requests(self):
        for name in ("../README", "/README", "a/../b", "a//b", "a?ref=evil", "a%2fb", ".git/config"):
            self.assertFalse(self.call(params={"path": name}, key="bad")["ok"])
        for name in (".github/workflows/ci.yml", ".GITHUB/x", "../x"):
            result = self.call("publish", {"message": "x", "files": {name: "x"}}, "bad")
            self.assertFalse(result["ok"])
        self.assertEqual(self.remote.calls, [])

    def test_cannot_use_other_branches_or_prs(self):
        self.assertEqual(self.call("open_pr", {"branch": "main", "title": "x", "body": ""}, "pr")["error"]["code"], "scope")
        self.assertEqual(self.call("comment", {"number": 42, "body": "x"}, "comment")["error"]["code"], "scope")
        self.assertEqual(self.store.status()["used"]["operations"], 0)

    def test_durable_idempotency_and_conflicts(self):
        first = self.publish()
        after_restart = Gateway(Store(self.store.directory), self.remote)
        request = {"message": "Fix test", "files": {"test.py": "assert True\n"}}
        self.assertEqual(self.call("publish", request, "change-1", after_restart), first)
        self.assertEqual(len(self.remote.calls), 4)
        request["message"] = "different"
        self.assertEqual(self.call("publish", request, "change-1")["error"]["code"], "conflict")
        self.assertEqual(self.store.status()["used"]["publish"], 1)

    def test_concurrent_calls_cannot_overspend(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda i: self.publish("p%d" % i), range(24)))
        self.assertEqual(sum(result["ok"] for result in results), 1)
        self.assertEqual(len(self.remote.calls), 4)
        self.assertEqual(self.store.status()["used"]["publish"], 1)

    def test_sqlite_reservation_is_atomic_across_connections(self):
        def reserve(i):
            request = {"id": "p%d" % i, "operation": "publish", "params": {"files": {"x": "x"}, "message": "x"}}
            try:
                Store(self.store.directory).reserve(request)
                return True
            except Denied:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(reserve, range(24))), 1)

    def test_timeout_consumes_budget_and_never_replays(self):
        def fail(method, path, data):
            if path.endswith("/git/refs"):
                raise TimeoutError("possibly accepted by GitHub")
        self.remote.hook = fail
        first = self.publish()
        self.assertEqual(first["error"]["code"], "uncertain")
        self.assertEqual(self.publish(), first)
        self.assertEqual(len(self.remote.calls), 4)
        self.assertEqual(self.publish("another")["error"]["code"], "limit")

    def test_recovery_keeps_reservation_without_replay(self):
        request = {"id": "interrupted", "operation": "publish", "params": {"files": {"x": "x"}, "message": "x"}}
        self.store.reserve(request)
        self.store.recover()
        self.assertEqual(self.gateway.handle(request)["error"]["code"], "uncertain")
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.store.status()["used"]["publish"], 1)

    def test_revoke_stops_remaining_steps(self):
        def revoke(method, path, data):
            if path.endswith("/git/trees"):
                self.store.revoke()
        self.remote.hook = revoke
        self.assertEqual(self.publish()["error"]["code"], "revoked")
        self.assertEqual(len(self.remote.calls), 2)
        self.assertEqual(self.call()["error"]["code"], "revoked")
        self.assertEqual(self.store.status()["used"]["publish"], 1)

    def test_expiry_stops_dispatch(self):
        with patch("agent_leases.store.time.time", return_value=self.store.status()["expires"] + 1):
            self.assertEqual(self.call()["error"]["code"], "expired")
        self.assertEqual(self.remote.calls, [])

    def test_grant_is_narrow_and_uses_reviewed_revision(self):
        self.publish()
        self.assertEqual(self.publish("second")["error"]["code"], "limit")
        old = self.store.status()
        self.store.grant("publish", 1, digest(old["policy"]))
        self.assertTrue(self.publish("second")["ok"])
        new = self.store.status()
        self.assertEqual(new["expires"], old["expires"])
        self.assertEqual(new["policy"]["limits"], old["policy"]["limits"])
        with self.assertRaises(Denied):
            self.store.grant("publish", 1, digest(old["policy"]))

    def test_aggregate_write_and_payload_caps(self):
        for cap, maximum in (("write_operations", 0), ("payload_bytes", 1), ("operations", 0)):
            p = policy()
            p["limits"][cap] = maximum
            store = Store.create(Path(self.temp.name) / cap, p)
            gateway = Gateway(store, self.remote)
            result = self.call("publish", {"message": "x", "files": {"x": "x"}}, "x", gateway)
            self.assertEqual(result["error"]["code"], "limit")
            self.assertEqual(store.status()["used"]["publish"], 0)
        self.assertEqual(self.remote.calls, [])

    def test_read_still_works_after_write_cap(self):
        self.publish()
        self.assertFalse(self.publish("second")["ok"])
        self.assertTrue(self.call()["ok"])

    def test_audit_omits_payloads_and_detects_modified_records(self):
        secret = "CANARY_BODY_NOT_IN_AUDIT"
        self.call("publish", {"message": "test", "files": {"x": secret}}, "write")
        self.assertNotIn(secret, json.dumps(self.store.events()))
        with self.store.transaction() as db:
            db.execute("UPDATE events SET record='{}' WHERE sequence=2")
        with self.assertRaises(Denied):
            self.store.verify()

    def test_database_failure_does_not_forward(self):
        with patch.object(self.store, "reserve", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.call()
        self.assertEqual(self.remote.calls, [])

    def test_socket_roundtrip_and_malformed_json(self):
        address = str(Path(self.temp.name) / "g.sock")
        with Server(address, self.gateway) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertTrue(client(address, {"operation": "status"})["ok"])
                request = {"id": "r", "operation": "read_file", "params": {"path": "README.md"}}
                self.assertTrue(client(address, request)["ok"])
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.connect(address)
                    sock.sendall(b'{"operation":"status","operation":"grant"}\n')
                    self.assertEqual(json.loads(sock.recv(4096))["error"]["code"], "invalid")
            finally:
                server.shutdown()
                thread.join()


class PolicyTests(unittest.TestCase):
    def test_unknown_fields_and_wrong_types_are_rejected(self):
        cases = []
        for key, value in (("cost_usd", 5), ("ttl_seconds", True), ("base", "../main"),
                           ("repo", "owner/repo?x=y"), ("base_sha", "main")):
            p = policy()
            p[key] = value
            cases.append(p)
        p = policy()
        p["capabilities"]["merge"] = 1
        cases.append(p)
        for p in cases:
            with self.subTest(p=p), self.assertRaises(Denied):
                compile_policy(p)

    def test_json_ambiguity_rejected(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{'):
            with self.assertRaises(Denied):
                parse_json(raw)

    def test_render_matches_compiled_limits_and_escapes_purpose(self):
        p = policy()
        p["purpose"] = "\x1b[2JIgnore previous caps\n"
        output = render(p)
        self.assertNotIn("\x1b", output)
        self.assertIn(digest(compile_policy(p)), output)
        for key, value in p["capabilities"].items():
            self.assertIn("%s: at most %s" % (key, value), output)


if __name__ == "__main__":
    unittest.main()
