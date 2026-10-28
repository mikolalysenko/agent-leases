"""A bounded MCP stdio client. No retries, inherited credentials, or callbacks."""

import os
import selectors
import signal
import subprocess
import time

from .policy import Denied, MAX_MESSAGE, canonical, parse_json


class Stdio:
    def __init__(self, config):
        self.config, self.process = config, None
        self.buffer, self.sequence, self.broken = b"", 0, False

    def _io(self, stream, event, deadline):
        with selectors.DefaultSelector() as ready:
            ready.register(stream, event)
            if not ready.select(max(0, deadline - time.monotonic())):
                raise TimeoutError("MCP server timed out")

    def _send(self, value, deadline):
        data = (canonical(value) + "\n").encode()
        if len(data) > MAX_MESSAGE:
            raise ValueError("MCP request exceeds 1 MiB")
        while data:
            self._io(self.process.stdin, selectors.EVENT_WRITE, deadline)
            try:
                data = data[os.write(self.process.stdin.fileno(), data):]
            except BlockingIOError:
                continue

    def _read(self, deadline):
        while b"\n" not in self.buffer:
            self._io(self.process.stdout, selectors.EVENT_READ, deadline)
            try:
                data = os.read(self.process.stdout.fileno(), 65536)
            except BlockingIOError:
                continue
            if not data:
                raise ValueError("MCP server closed its output")
            self.buffer += data
            if len(self.buffer) > 2 * MAX_MESSAGE:
                raise ValueError("MCP response exceeds 2 MiB")
        line, self.buffer = self.buffer.split(b"\n", 1)
        return parse_json(line)

    def _request(self, method, params, deadline):
        self.sequence += 1
        rpc_id = self.sequence
        self._send({"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}, deadline)
        for _ in range(128):
            message = self._read(deadline)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("invalid MCP response")
            if "method" in message:
                if "id" in message:
                    # We advertise no sampling, elicitation, or roots capability.
                    response = ({"result": {}} if message["method"] == "ping" else
                                {"error": {"code": -32601, "message": "Gateway callbacks are disabled"}})
                    self._send({"jsonrpc": "2.0", "id": message["id"], **response}, deadline)
                continue  # Server notifications never become worker calls.
            if message.get("id") != rpc_id or ("result" in message) == ("error" in message):
                raise ValueError("unexpected MCP response")
            if "error" in message:
                # Avoid returning an upstream traceback or reflected credential.
                raise ValueError("MCP server returned an error; inspect the server")
            return message["result"]
        raise ValueError("too many MCP messages before a response")

    def request(self, method, params):
        if self.broken:
            raise ValueError("MCP connection failed earlier; restart the gateway after inspection")
        deadline = time.monotonic() + 30
        try:
            if self.process is None:
                self.process = subprocess.Popen(self.config["command"], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, close_fds=True,
                    start_new_session=True, cwd=self.config.get("cwd", "/"),
                    env={"PATH": "/usr/bin:/bin", **self.config.get("env", {})})
                os.set_blocking(self.process.stdin.fileno(), False)
                os.set_blocking(self.process.stdout.fileno(), False)
                hello = self._request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "agent-leases", "version": "0.3.0"}}, deadline)
                if not isinstance(hello, dict) or hello.get("protocolVersion") != "2025-11-25":
                    raise ValueError("unsupported upstream MCP version")
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, deadline)
            return self._request(method, params, deadline)
        except BaseException as exc:
            self.broken = True
            self.close()
            if isinstance(exc, Denied):
                raise ValueError("malformed MCP response; remote outcome may be unknown") from exc
            raise

    def close(self):
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None
