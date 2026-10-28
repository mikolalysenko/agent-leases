"""Service plugins. Only installed, operator-trusted code can define an adapter."""

from importlib.metadata import entry_points

from ..policy import Denied, fields, string


def adapter_type(name):
    if name == "github":
        from .github import Adapter
        return Adapter
    if name == "mcp":
        from .mcp import Adapter
        return Adapter
    plugins = entry_points()
    plugins = (plugins.select(group="agent_leases.adapters") if hasattr(plugins, "select")
               else plugins.get("agent_leases.adapters", []))
    matches = [plugin for plugin in plugins if plugin.name == name]
    if len(matches) != 1:
        raise Denied("invalid", "adapter is not installed: " + name)
    return matches[0].load()


def services(policy):
    if policy["version"] == "mvp-1":
        return {"": {"adapter": "github", **{k: policy[k] for k in ("repo", "base", "base_sha")}}}
    return policy["services"]


def definitions(policy):
    result = {}
    for service, config in services(policy).items():
        string(config.get("adapter"), "adapter", pattern=r"[a-z][a-z0-9_-]*")
        kind = adapter_type(config["adapter"])
        options = {k: v for k, v in config.items() if k != "adapter"}
        kind.compile(options)
        for op, spec in kind.operations(options).items():
            string(op, "operation", maximum=64, pattern=r"[a-z][a-z0-9_]*")
            if type(spec["write"]) is not bool:
                raise Denied("invalid", "adapter must classify each operation")
            name = service + "." + op if service else op
            result[name] = {**spec, "service": service, "local_name": op,
                            "kind": kind, "options": options}
    return result


def catalog(policy):
    return {name: {key: spec[key] for key in ("description", "inputSchema", "write")}
            for name, spec in definitions(policy).items() if name in policy["capabilities"]}


def binding(policy, operation):
    if operation not in policy["capabilities"]:
        raise Denied("unknown_operation", "operation is not allowlisted")
    return definitions(policy)[operation]


def connect(policy, config):
    """Runtime configuration (commands and credentials) never comes from the worker."""
    result = {}
    try:
        for service, definition in services(policy).items():
            kind = adapter_type(definition["adapter"])
            options = {k: v for k, v in definition.items() if k != "adapter"}
            # Keep the original embedded Python API usable for the existing test matrix.
            if not isinstance(config, dict):
                if policy["version"] != "mvp-1":
                    raise ValueError("service configuration must be an object")
                instance = object.__new__(kind)
                instance.options, instance.remote = options, config
            else:
                key = service or "github"
                if key not in config:
                    raise ValueError("missing gateway configuration for service " + key)
                instance = kind(options, config[key])
            result[service] = instance
        return result
    except BaseException:
        for instance in result.values():
            instance.close()
        raise
