"""Map approved operations to a trusted MCP server, including non-tool methods."""

from pathlib import Path

from ..policy import Denied, canonical, fields, parse_json, string
from ..stdio import Stdio


class Adapter:
    @staticmethod
    def compile(options):
        fields(options, ("operations",))
        operations = options["operations"]
        if not isinstance(operations, dict) or not 1 <= len(operations) <= 128:
            raise Denied("invalid", "MCP adapter requires 1–128 operation bindings")
        for name, spec in operations.items():
            string(name, "operation", maximum=64, pattern=r"[a-z][a-z0-9_]*")
            fields(spec, ("method", "params", "input", "write"))
            method = string(spec["method"], "MCP method", pattern=r"[A-Za-z][A-Za-z0-9_/]*")
            if method == "initialize" or method.startswith("notifications/"):
                raise Denied("invalid", "MCP lifecycle and notifications cannot be leased")
            if not isinstance(spec["params"], dict) or type(spec["write"]) is not bool:
                raise Denied("invalid", "MCP params must be an object and write must be boolean")
            target = spec["input"]
            if target is not None:
                string(target, "input slot", minimum=0, maximum=64, pattern=r"[A-Za-z0-9_]*")
                if target and not isinstance(spec["params"].get(target, {}), dict):
                    raise Denied("invalid", "MCP input slot must contain an object")

    @staticmethod
    def operations(options):
        return {name: {"description": "MCP %s; fixed params %s; input slot %s" % (
                          spec["method"], canonical(spec["params"]), canonical(spec["input"])),
                       "write": spec["write"], "inputSchema": {"type": "object",
                           "additionalProperties": spec["input"] is not None}}
                for name, spec in options["operations"].items()}

    @staticmethod
    def describe(options):
        lines = ["Trusted MCP server runs outside the worker; its implementation defines the effects."]
        for name, spec in options["operations"].items():
            lines.append("%s: %s %s; %s; %s" % (name, spec["method"], canonical(spec["params"]),
                         "write" if spec["write"] else "declared read-only",
                         "no worker arguments" if spec["input"] is None else
                         "other fields in %s are unrestricted" % canonical(spec["input"] or "params")))
        return lines

    @staticmethod
    def validate(options, op, params):
        spec = options["operations"][op]
        if not isinstance(params, dict):
            raise Denied("invalid", "operation params must be an object")
        target = spec["input"]
        if target is None:
            fields(params, ())
        else:
            fixed = spec["params"].get(target, {}) if target else spec["params"]
            if params.keys() & fixed.keys():
                raise Denied("scope", "MCP arguments cannot replace fixed parameters")
        return None

    def __init__(self, options, config):
        fields(config, ("command",), ("cwd", "env"))
        if not isinstance(config["command"], list) or not config["command"]:
            raise ValueError("MCP command must be a nonempty argv array")
        for arg in config["command"]:
            string(arg, "MCP command argument", maximum=4096)
        if not Path(config["command"][0]).is_absolute():
            raise ValueError("MCP executable must be an absolute path")
        if "cwd" in config and not Path(config["cwd"]).is_absolute():
            raise ValueError("MCP cwd must be absolute")
        if not isinstance(config.get("env", {}), dict):
            raise ValueError("MCP env must be an object")
        for key, value in config.get("env", {}).items():
            string(key, "environment name", pattern=r"[A-Za-z_][A-Za-z0-9_]*")
            string(value, "environment value", minimum=0, maximum=32768)
        self.options, self.remote = options, Stdio(config)

    def execute(self, context, op, params):
        spec = self.options["operations"][op]
        data = parse_json(canonical(spec["params"]))
        target = spec["input"]
        if target is not None:
            (data.setdefault(target, {}) if target else data).update(params)
        # Log the binding, not argument bodies or credentials.
        context.dispatch(spec["method"], context.service + "." + op)
        result = self.remote.request(spec["method"], data)
        if isinstance(result, dict) and result.get("isError"):
            raise Denied("upstream", "MCP tool reported failure; the attempt remains charged")
        return result, None

    def close(self):
        self.remote.close()
