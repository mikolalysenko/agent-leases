"""Strict input validation. Unknown fields never silently become permissions."""

import hashlib
import json
import re

OPERATIONS = ("read_file", "publish", "open_pr", "comment")
LIMITS = ("operations", "write_operations", "payload_bytes")
MAX_MESSAGE = 1024 * 1024


class Denied(Exception):
    def __init__(self, code, rule, **details):
        self.data = {"code": code, "rule": rule, "retryable": False, **details}
        super().__init__(rule)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def parse_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise Denied("invalid", "duplicate JSON field")
            result[key] = item
        return result

    def constant(_):
        raise Denied("invalid", "non-finite JSON number")

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Denied("invalid", "invalid JSON") from exc


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        raise Denied("invalid", "missing required fields")
    if value.keys() - set(required) - set(optional):
        raise Denied("invalid", "unknown fields")


def integer(value, name, minimum=0, maximum=10**9):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Denied("invalid", "invalid " + name)
    return value


def string(value, name, minimum=1, maximum=1000, pattern=None):
    if (not isinstance(value, str) or not minimum <= len(value) <= maximum
            or (pattern and not re.fullmatch(pattern, value))):
        raise Denied("invalid", "invalid " + name)
    # Surrogate code points have no UTF-8 representation.
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise Denied("invalid", "invalid " + name) from exc
    return value


def path(value, writing=False):
    string(value, "file path", maximum=240, pattern=r"[A-Za-z0-9_./ -]+")
    parts = value.split("/")
    if any(part in ("", ".", "..") or part.lower() == ".git" for part in parts):
        raise Denied("scope", "file path must be relative and canonical")
    if writing and parts[0].lower() == ".github":
        raise Denied("scope", "writing .github is not supported")
    return value


def compile_policy(value):
    fields(value, ("version", "purpose", "repo", "base", "base_sha", "ttl_seconds",
                   "capabilities", "limits"))
    if value["version"] != "mvp-1":
        raise Denied("invalid", "unsupported policy version")
    string(value["purpose"], "purpose")
    string(value["repo"], "repo", maximum=200,
           pattern=r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")
    base = string(value["base"], "base", maximum=100, pattern=r"[A-Za-z0-9_/-]+")
    if any(not part for part in base.split("/")):
        raise Denied("invalid", "invalid base")
    string(value["base_sha"], "base_sha", maximum=40, pattern=r"[0-9a-f]{40}")
    integer(value["ttl_seconds"], "ttl_seconds", 1, 86400)
    fields(value["capabilities"], OPERATIONS)
    fields(value["limits"], LIMITS)
    for key, cap in {**value["capabilities"], **value["limits"]}.items():
        integer(cap, key)
    return parse_json(canonical(value))


def render(policy):
    policy = compile_policy(policy)
    lines = ["AGENT LEASE — compiled policy", "Policy SHA-256: " + digest(policy),
             "Agent's purpose (grants nothing): " + canonical(policy["purpose"]),
             "Repository: " + policy["repo"],
             "Read/publish parent: " + policy["base_sha"],
             "Draft PR base: " + policy["base"],
             "Expires: %s seconds after approval" % policy["ttl_seconds"]]
    lines += ["%s: at most %s attempts" % (op, policy["capabilities"][op])
              for op in OPERATIONS]
    lines += ["%s: %s" % (key, policy["limits"][key]) for key in LIMITS]
    lines += ["Publish creates a fresh lease/<id>/<request-id> branch; no ref updates.",
              "Comments are limited to PRs this lease created.",
              "Risk: writes may expose any data the worker can read.",
              "Risk: branches and PRs can trigger CI, webhooks, and notifications.",
              "Use a disposable repository with Actions disabled and no privileged integrations.",
              "Failed/uncertain attempts consume budget. In-flight writes cannot be recalled."]
    return "\n".join(lines)


def validate_request(request):
    fields(request, ("id", "operation", "params"))
    string(request["id"], "request id", maximum=64, pattern=r"[A-Za-z0-9_-]+")
    op, params = request["operation"], request["params"]
    if op not in OPERATIONS:
        raise Denied("unknown_operation", "operation is not allowlisted")
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
    return request


def starter_policy(repo, commit, base="main", ttl_seconds=3600, purpose="Make a small change"):
    return compile_policy({
        "version": "mvp-1", "purpose": purpose, "repo": repo, "base": base,
        "base_sha": commit, "ttl_seconds": ttl_seconds,
        "capabilities": {"read_file": 20, "publish": 1, "open_pr": 1, "comment": 2},
        "limits": {"operations": 24, "write_operations": 4, "payload_bytes": 1048576},
    })
