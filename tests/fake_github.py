"""A recording provider at the HTTP request boundary, never production-selectable."""

import base64
import copy


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.hook = None

    def request(self, repo, permissions, method, path, data=None):
        self.calls.append((repo, copy.deepcopy(permissions), method, path, copy.deepcopy(data)))
        if self.hook:
            self.hook(method, path, data)
        if "/contents/" in path:
            return {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(b"# A test repository\n").decode()}
        if method == "GET" and "/git/commits/" in path:
            return {"tree": {"sha": "a" * 40}}
        if path.endswith("/git/trees"):
            return {"sha": "b" * 40}
        if path.endswith("/git/commits"):
            return {"sha": "c" * 40}
        if path.endswith("/git/refs"):
            return {"ref": data["ref"]}
        if path.endswith("/pulls"):
            return {"number": 7, "html_url": "https://untrusted.example/not-returned"}
        if path.endswith("/comments"):
            return {"id": 23}
        raise AssertionError("unexpected upstream request: " + path)
