"""MCP stdio is a client of the lease socket. It has no administrative authority."""

import sys

from .gateway import client, operation_request
from .policy import Denied, MAX_MESSAGE, canonical, fields, parse_json, string

TEXT = {"type": "string"}
CONTROLS = {
    "status": ("Show the lease, budgets, and time remaining.", {}),
    "ask": ("Send the operator a question. Returns an ID to poll.", {"message": TEXT}),
    "request": ("Ask for more of one existing budget. Grants nothing until the operator approves.",
                {"cap": TEXT, "amount": {"type": "integer", "minimum": 1}, "message": TEXT}),
    "poll": ("Read the operator's answer without blocking other work.", {"id": TEXT}),
}
INSTRUCTIONS = ("Start with lease_status. Use only the granted tools. Identical calls reuse their recorded result. "
                "Use request_id only for intentional new attempts. On limit use lease_request, then lease_poll. "
                "Use lease_ask for questions. On uncertain, stop and ask them to inspect the service. "
                "You cannot approve or extend a lease.")


def tool_list(catalog):
    result = []
    for name, (description, properties) in CONTROLS.items():
        optional = {"request_id": TEXT} if name in ("ask", "request") else {}
        result.append({"name": "lease_" + name, "description": description,
                       "inputSchema": {"type": "object", "properties": {**properties, **optional},
                                       "required": list(properties), "additionalProperties": False}})
    for name, spec in catalog.items():
        schema = spec["inputSchema"]
        # New service operations wrap their parameters so an upstream field named
        # request_id cannot collide with the lease's replay identifier.
        if "." in name:
            schema = {"type": "object", "properties": {"params": schema},
                      "required": ["params"], "additionalProperties": False}
        schema = {**schema, "properties": {**schema.get("properties", {}), "request_id": TEXT}}
        result.append({"name": "lease_" + name, "description": spec["description"], "inputSchema": schema})
    return result


def submission(kind, message, cap=None, amount=None, request_id=None):
    from .policy import digest
    body = {"kind": kind, "message": message}
    if kind == "more":
        body.update(cap=cap, amount=amount)
    body["id"] = request_id if request_id is not None else digest(body)
    return {"operation": "submit", "request": body}


class Session:
    def __init__(self, address):
        self.address = address
        self.initialized = False
        self.ready = False
        self.catalog = None

    def get_catalog(self):
        if self.catalog is None:
            response = client(self.address, {"operation": "catalog"})
            if not response["ok"]:
                raise Denied("unavailable", "cannot read the gateway's operation catalog")
            self.catalog = response["result"]
        return self.catalog

    def handle(self, message):
        rpc_id = message.get("id") if isinstance(message, dict) else None
        try:
            fields(message, ("jsonrpc", "method"), ("id", "params"))
            if message["jsonrpc"] != "2.0" or not isinstance(message["method"], str):
                raise Denied("invalid", "invalid JSON-RPC request")
            if "id" not in message:
                if message["method"] == "notifications/initialized" and self.initialized:
                    self.ready = True
                return None  # Notifications must never perform tool calls.
            if type(rpc_id) not in (int, str):
                raise Denied("invalid", "invalid JSON-RPC id")
            method, params = message["method"], message.get("params", {})
            if method == "initialize" and not self.initialized:
                fields(params, ("protocolVersion", "capabilities", "clientInfo"), ("_meta",))
                self.initialized = True
                result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "agent-leases", "version": "0.3.0"},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif not self.ready:
                raise Denied("invalid", "initialize the MCP session first")
            elif method == "tools/list":
                fields(params, ())
                result = {"tools": tool_list(self.get_catalog())}
            elif method == "tools/call":
                fields(params, ("name",), ("arguments", "_meta"))
                name = params["name"]
                if not isinstance(name, str) or not name.startswith("lease_"):
                    raise Denied("unknown_operation", "unknown lease tool")
                op = name[6:]
                if op not in CONTROLS and op not in self.get_catalog():
                    raise Denied("unknown_operation", "unknown lease tool")
                arguments = params.get("arguments", {})
                try:
                    if not isinstance(arguments, dict):
                        raise Denied("invalid", "tool arguments must be an object")
                    if op in CONTROLS:
                        fields(arguments, tuple(CONTROLS[op][1]),
                               ("request_id",) if op in ("ask", "request") else ())
                    args = dict(arguments)
                    key = args.pop("request_id", None)
                    if "request_id" in arguments:
                        string(key, "request id", maximum=64, pattern=r"[A-Za-z0-9_-]+")
                    if op == "status":
                        request = {"operation": "status"}
                    elif op in ("ask", "request"):
                        request = submission("ask" if op == "ask" else "more", args["message"],
                                             args.get("cap"), args.get("amount"), key)
                    elif op == "poll":
                        request = {"operation": "poll", "request_id": args["id"]}
                    else:
                        if "." in op:
                            fields(args, ("params",))
                            args = args["params"]
                        request = operation_request(op, args, key)
                    response = client(self.address, request)
                except Denied as exc:
                    response = {"ok": False, "error": exc.data}
                except (OSError, ValueError):
                    response = {"ok": False, "error": {"code": "unavailable", "rule": "lease gateway is unavailable", "retryable": False}}
                result = {"content": [{"type": "text", "text": canonical(response)}],
                          "structuredContent": response, "isError": not response["ok"]}
            else:
                return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Unknown method"}}
            return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
        except Denied as exc:
            return {"jsonrpc": "2.0", "id": rpc_id if type(rpc_id) in (int, str) else None,
                    "error": {"code": -32602, "message": exc.data["rule"]}}
        except (OSError, ValueError):
            return {"jsonrpc": "2.0", "id": rpc_id, "error": {
                "code": -32603, "message": "lease gateway is unavailable"}}


def serve_mcp(address):
    session = Session(address)
    while True:
        line = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if not line:
            return
        try:
            if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                return  # Do not reinterpret a suffix of an oversized message.
            response = session.handle(parse_json(line))
        except Denied:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}
        if response is not None:
            print(canonical(response), flush=True)
