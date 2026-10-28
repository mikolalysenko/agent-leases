"""MCP stdio is a client of the lease socket. It has no administrative authority."""

import sys

from .gateway import client, operation_request
from .policy import Denied, MAX_MESSAGE, canonical, fields, parse_json, string

TEXT = {"type": "string"}
TOOLS = {
    "status": ("Show the lease, budgets, and time remaining.", {}),
    "read_file": ("Read a UTF-8 file at the approved commit.", {"path": TEXT}),
    "publish": ("Publish text files on a new lease branch; returns its name.",
                {"files": {"type": "object", "additionalProperties": TEXT}, "message": TEXT}),
    "open_pr": ("Open a draft PR from a branch this lease published.",
                {"branch": TEXT, "title": TEXT, "body": TEXT}),
    "comment": ("Comment on a PR this lease created.", {"number": {"type": "integer", "minimum": 1}, "body": TEXT}),
}
INSTRUCTIONS = ("Start with lease_status. Use only the granted tools. Identical calls reuse their recorded result. "
                "Use request_id only for intentional new attempts. On limit, ask the operator for more budget. "
                "On uncertain, stop and ask them to inspect GitHub. You cannot approve or extend a lease.")


def tool_list():
    result = []
    for name, (description, properties) in TOOLS.items():
        optional = {} if name == "status" else {"request_id": {**TEXT, "description": "Optional ID for an intentional new attempt."}}
        result.append({"name": "lease_" + name, "description": description,
                       "inputSchema": {"type": "object", "properties": {**properties, **optional},
                                       "required": list(properties), "additionalProperties": False}})
    return result


class Session:
    def __init__(self, address):
        self.address = address
        self.initialized = False
        self.ready = False

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
                          "serverInfo": {"name": "agent-leases", "version": "0.2.0"},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif not self.ready:
                raise Denied("invalid", "initialize the MCP session first")
            elif method == "tools/list":
                result = {"tools": tool_list()}
            elif method == "tools/call":
                fields(params, ("name",), ("arguments", "_meta"))
                name = params["name"]
                if not isinstance(name, str) or not name.startswith("lease_") or name[6:] not in TOOLS:
                    raise Denied("unknown_operation", "unknown lease tool")
                op = name[6:]
                arguments = params.get("arguments", {})
                try:
                    fields(arguments, tuple(TOOLS[op][1]), () if op == "status" else ("request_id",))
                    args = dict(arguments)
                    key = args.pop("request_id", None)
                    if "request_id" in arguments:
                        string(key, "request id", maximum=64, pattern=r"[A-Za-z0-9_-]+")
                    request = {"operation": "status"} if op == "status" else operation_request(op, args, key)
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
