"""Run from the repo: python3 -m examples.demo. No credentials or network used."""

import tempfile
from pathlib import Path

from agent_leases.gateway import Gateway
from agent_leases.policy import canonical, digest
from agent_leases.store import Store
from tests.fake_github import FakeGitHub
from tests.test_gateway import policy


def main():
    with tempfile.TemporaryDirectory(prefix="lease-demo-") as directory:
        store = Store.create(Path(directory) / "state", policy())
        remote = FakeGitHub()
        gateway = Gateway(store, remote)

        def call(key, operation, params):
            response = gateway.handle({"id": key, "operation": operation, "params": params})
            print(operation + ": " + canonical(response))
            return response

        change = {"message": "Demonstrate a lease", "files": {"hello.txt": "Hello!\n"}}
        published = call("change-1", "publish", change)
        assert published["ok"]
        assert call("change-1", "publish", change) == published  # No second remote write.
        assert call("change-2", "publish", change)["error"]["code"] == "limit"
        assert call("read-1", "read_file", {"path": "README.md"})["ok"]
        call("pr-1", "open_pr", {"branch": published["result"]["branch"], "title": "Hello", "body": "Demo"})
        store.grant("publish", 1, digest(store.status()["policy"]))
        assert call("change-2", "publish", change)["ok"]
        store.revoke()
        assert call("read-2", "read_file", {"path": "README.md"})["error"]["code"] == "revoked"
        print("Audit head: " + store.verify())
        print("Provider calls: %d (all simulated)" % len(remote.calls))


if __name__ == "__main__":
    main()
