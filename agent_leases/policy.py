"""Strict input validation. Unknown fields never silently become permissions."""

import hashlib
import json
import math
import re

OPERATIONS = ("read_file", "publish", "open_pr", "comment")
LIMITS = ("operations", "write_operations", "payload_bytes")
MAX_MESSAGE = 1024 * 1024


class Denied(Exception):
    def __init__(self, code, rule, **details):
        self.data = {"code": code, "rule": rule, "retryable": False, **details}
        super().__init__(rule)


class UpstreamError(Exception):
    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


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

    def floating(value):
        number = float(value)
        if not math.isfinite(number):
            constant(value)
        return number

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
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
    from .adapters import definitions
    if not isinstance(value, dict):
        raise Denied("invalid", "policy must be an object")
    common = ("version", "purpose", "ttl_seconds", "capabilities", "limits")
    if value.get("version") == "mvp-1":
        fields(value, common + ("repo", "base", "base_sha"))
        fields(value["capabilities"], OPERATIONS)
    elif value.get("version") == "mvp-2":
        fields(value, common + ("services",))
        if not isinstance(value["services"], dict) or not 1 <= len(value["services"]) <= 16:
            raise Denied("invalid", "policy requires 1–16 services")
        for name, config in value["services"].items():
            string(name, "service name", maximum=32, pattern=r"[a-z][a-z0-9_]*")
            if not isinstance(config, dict):
                raise Denied("invalid", "service must be an object")
    else:
        raise Denied("invalid", "unsupported policy version")
    string(value["purpose"], "purpose")
    integer(value["ttl_seconds"], "ttl_seconds", 1, 86400)
    available = definitions(value)
    caps = value["capabilities"]
    if not isinstance(caps, dict) or not caps or caps.keys() - available.keys():
        raise Denied("invalid", "capabilities must name adapter operations")
    fields(value["limits"], LIMITS)
    for key, cap in {**caps, **value["limits"]}.items():
        integer(cap, key)
    return parse_json(canonical(value))


def render(policy):
    from .adapters import adapter_type, services
    policy = compile_policy(policy)
    lines = ["AGENT LEASE — compiled policy", "Policy SHA-256: " + digest(policy),
             "Agent's purpose (grants nothing): " + canonical(policy["purpose"]),
             "Expires: %s seconds after approval" % policy["ttl_seconds"]]
    for name, config in services(policy).items():
        lines.append("Service: " + (name or "github") + " (" + config["adapter"] + ")")
        kind = adapter_type(config["adapter"])
        lines += kind.describe({k: v for k, v in config.items() if k != "adapter"})
    lines += ["%s: at most %s attempts" % (op, cap) for op, cap in policy["capabilities"].items()]
    lines += ["%s: %s" % (key, policy["limits"][key]) for key in LIMITS]
    lines += ["Risk: writes may expose any data the worker can read.",
              "Failed/uncertain attempts consume budget. In-flight writes cannot be recalled."]
    return "\n".join(lines)


def validate_request(request, policy=None):
    fields(request, ("id", "operation", "params"))
    string(request["id"], "request id", maximum=64, pattern=r"[A-Za-z0-9_-]+")
    string(request["operation"], "operation", maximum=100, pattern=r"[a-z][a-z0-9_.]*")
    if not isinstance(request["params"], dict):
        raise Denied("invalid", "operation params must be an object")
    if policy is not None:
        from .adapters import binding
        spec = binding(policy, request["operation"])
        spec["kind"].validate(spec["options"], spec["local_name"], request["params"])
    return request


def starter_policy(repo, commit, base="main", ttl_seconds=3600, purpose="Make a small change"):
    return compile_policy({
        "version": "mvp-1", "purpose": purpose, "repo": repo, "base": base,
        "base_sha": commit, "ttl_seconds": ttl_seconds,
        "capabilities": {"read_file": 20, "publish": 1, "open_pr": 1, "comment": 2},
        "limits": {"operations": 24, "write_operations": 4, "payload_bytes": 1048576},
    })
