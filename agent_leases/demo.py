"""An offline example. The fake GitHub is never selectable by the real server."""

import tempfile
from pathlib import Path

from .gateway import Gateway, operation_request
from .policy import digest, starter_policy
from .store import Store


class ExampleGitHub:
    def __init__(self):
        self.writes = 0

    def request(self, method, path, data=None):
        self.writes += method == "POST"
        if method == "GET":
            return {"tree": {"sha": "a" * 40}}
        if path.endswith("/pulls"):
            return {"number": 1}
        return {"sha": "b" * 40}


def main():
    with tempfile.TemporaryDirectory(prefix="lease-demo-") as directory:
        store = Store.create(Path(directory) / "state", starter_policy("example/demo", "a" * 40))
        remote = ExampleGitHub()
        gateway = Gateway(store, remote)
        params = {"message": "Fix a test", "files": {"test.txt": "Fixed\n"}}
        first = operation_request("publish", params)
        result = gateway.handle(first)
        assert result["ok"]
        print("1. Approved: one repo, one publish, one draft PR, two comments, one hour.")
        print("2. Published a change on its own lease branch (simulated GitHub).")
        assert gateway.handle(first) == result and remote.writes == 3
        print("3. Repeated the command: returned the same branch, with no second write.")
        second = operation_request("publish", params, "another-change")
        assert gateway.handle(second)["error"]["code"] == "limit"
        print("4. A new publish was refused: no publish budget left.")
        pr = gateway.handle(operation_request("open_pr", {"branch": result["result"]["branch"], "title": "Fix a test", "body": "Tested."}))
        assert pr["ok"]
        print("5. Opening the draft PR still worked: its budget was separate.")
        store.grant("publish", 1, digest(store.status()["policy"]))
        assert gateway.handle(second)["ok"]
        print("6. The operator granted one more publish; the new change went through.")
        store.revoke()
        assert gateway.handle(first)["error"]["code"] == "revoked"
        print("7. Revoked: further work was refused. No token or network was used.")
