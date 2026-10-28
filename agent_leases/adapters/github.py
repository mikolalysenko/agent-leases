"""GitHub's scopes, validation, and effects live here, outside the lease engine."""

import base64
from urllib.parse import quote

from ..github import GitHub
from ..policy import Denied, fields, integer, path, string

OPERATIONS = ("read_file", "publish", "open_pr", "comment")
TEXT = {"type": "string"}
SHAPES = {
    "read_file": ("Read a UTF-8 file at the approved commit.", {"path": TEXT}),
    "publish": ("Publish text files to a fresh lease branch.",
                {"files": {"type": "object", "additionalProperties": TEXT}, "message": TEXT}),
    "open_pr": ("Open a draft PR from a branch created by this lease.",
                {"branch": TEXT, "title": TEXT, "body": TEXT}),
    "comment": ("Comment on a PR created by this lease.",
                {"number": {"type": "integer", "minimum": 1}, "body": TEXT}),
}


class Adapter:
    @staticmethod
    def compile(options):
        fields(options, ("repo", "base", "base_sha"))
        string(options["repo"], "repo", maximum=200,
               pattern=r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")
        base = string(options["base"], "base", maximum=100, pattern=r"[A-Za-z0-9_/-]+")
        if any(not part for part in base.split("/")):
            raise Denied("invalid", "invalid base")
        string(options["base_sha"], "base_sha", maximum=40, pattern=r"[0-9a-f]{40}")

    @staticmethod
    def operations(options):
        return {op: {"description": desc, "write": op != "read_file", "inputSchema": {
            "type": "object", "properties": props, "required": list(props), "additionalProperties": False}}
            for op, (desc, props) in SHAPES.items()}

    @staticmethod
    def describe(options):
        return ["Repository: " + options["repo"], "Read/publish parent: " + options["base_sha"],
                "Draft PR base: " + options["base"],
                "Publish creates fresh lease branches. Comments require a PR created by this lease.",
                "Risk: branches and PRs can trigger CI, webhooks, and notifications.",
                "Use a disposable repository with Actions disabled and no privileged integrations."]

    @staticmethod
    def validate(options, op, params):
        if op == "read_file":
            fields(params, ("path",))
            path(params["path"])
        elif op == "publish":
            fields(params, ("files", "message"))
            string(params["message"], "commit message", maximum=2000)
            if not isinstance(params["files"], dict) or not 1 <= len(params["files"]) <= 50:
                raise Denied("invalid", "publish requires 1–50 text files")
            for name, content in params["files"].items():
                path(name, writing=True)
                string(content, "file contents", minimum=0, maximum=256 * 1024)
            names = set(params["files"])
            if any("/".join(name.split("/")[:i]) in names
                   for name in names for i in range(1, len(name.split("/")))):
                raise Denied("invalid", "conflicting file paths")
        elif op == "open_pr":
            fields(params, ("branch", "title", "body"))
            string(params["branch"], "branch", maximum=200, pattern=r"[A-Za-z0-9_/-]+")
            string(params["title"], "title", maximum=200)
            string(params["body"], "body", minimum=0, maximum=32000)
        else:
            fields(params, ("number", "body"))
            integer(params["number"], "PR number", 1)
            string(params["body"], "body", maximum=32000)
        if op == "open_pr":
            return ("branch", params["branch"])
        if op == "comment":
            return ("pr", str(params["number"]))
        return None

    def __init__(self, options, config):
        fields(config, ("token_file",))
        self.options = options
        self.remote = GitHub(options["repo"], config["token_file"])

    def close(self):
        pass

    def execute(self, context, op, params):
        policy, lease, request = self.options, context.lease, context.request
        repo = policy["repo"]
        root = "/repos/" + repo

        def call(method, suffix, data=None):
            context.dispatch(method, root + suffix)
            return self.remote.request(method, root + suffix, data)

        stamp = "\n\nLease: %s\nLease-Request: %s" % (lease["id"], request["id"])
        if op == "read_file":
            result = call("GET", "/contents/" + quote(params["path"], safe="/")
                          + "?ref=" + policy["base_sha"])
            if not isinstance(result, dict) or result.get("encoding") != "base64":
                raise ValueError("only small, ordinary files are supported")
            content = base64.b64decode(result["content"].replace("\n", ""), validate=True)
            if result.get("type") != "file" or len(content) > 256 * 1024:
                raise ValueError("only small, ordinary files are supported")
            return {"path": params["path"], "content": content.decode("utf-8")}, None
        if op == "publish":
            parent = call("GET", "/git/commits/" + policy["base_sha"])
            tree = call("POST", "/git/trees", {
                "base_tree": parent["tree"]["sha"],
                "tree": [{"path": name, "mode": "100644", "type": "blob", "content": content}
                         for name, content in sorted(params["files"].items())]})
            commit = call("POST", "/git/commits", {
                "message": params["message"] + stamp, "tree": tree["sha"],
                "parents": [policy["base_sha"]]})
            branch = "lease/%s/%s" % (lease["id"], request["id"])
            call("POST", "/git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
            return {"branch": branch, "sha": commit["sha"]}, ("branch", branch)
        if op == "open_pr":
            result = call("POST", "/pulls", {"head": params["branch"], "base": policy["base"],
                          "title": params["title"], "body": params["body"] + stamp,
                          "draft": True, "maintainer_can_modify": False})
            number = result["number"]
            if type(number) is not int or number < 1:
                raise ValueError("invalid upstream PR number")
            return {"number": number, "url": "https://github.com/%s/pull/%d" % (repo, number)}, ("pr", str(number))
        call("POST", "/issues/%d/comments" % params["number"], {"body": params["body"] + stamp})
        return {"number": params["number"], "commented": True}, None

