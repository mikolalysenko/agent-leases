"""Four operations, one socket, one lease. Administrative operations are local only."""

import base64
import fcntl
import socket
import socketserver
import threading
from urllib.parse import quote

from .policy import (Denied, MAX_MESSAGE, canonical, fields, parse_json,
                     validate_request)


class Gateway:
    def __init__(self, store, github):
        self.store, self.github = store, github
        # Serialize operations, keeping the broker and multi-step publishes simple.
        # Admin revocation/grants use independent SQLite transactions.
        self.lock = threading.Lock()

    def handle(self, request):
        try:
            if isinstance(request, dict) and request.get("operation") == "status":
                fields(request, ("operation",))
                return {"ok": True, "result": self.store.status()}
            validate_request(request)
            with self.lock:
                cached = self.store.reserve(request)
                if cached is not None:
                    return cached
                try:
                    result, owned = self.execute(request)
                    response = {"ok": True, "result": result}
                except Denied as exc:
                    response, owned = self.store.denial(exc), None
                except Exception:
                    # A timeout, bad response, or even a crash after a POST can mean
                    # the write succeeded. Preserve the reservation and never replay.
                    response, owned = self.store.denial(Denied(
                        "uncertain", "upstream operation failed; inspect GitHub before new work")), None
                self.store.finish(request, response, owned)
                return response
        except Denied as exc:
            return self.store.denial(exc)

    def execute(self, request):
        lease = self.store.status()
        policy, op, params = lease["policy"], request["operation"], request["params"]
        repo = policy["repo"]
        root = "/repos/" + repo
        permissions = ({"contents": "write"} if op == "publish" else
                       {"contents": "read"} if op == "read_file" else
                       {"pull_requests": "write"})

        def call(method, suffix, data=None):
            self.store.dispatch(request["id"], method, root + suffix)
            return self.github.request(repo, permissions, method, root + suffix, data)

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


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, gateway):
        self.gateway = gateway
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Never put worker payloads or credentials in a traceback on stdout.
        pass


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            line = self.rfile.readline(MAX_MESSAGE + 1)
            if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                raise Denied("invalid", "request must be a JSON line smaller than 1 MiB")
            response = self.server.gateway.handle(parse_json(line))
        except Denied as exc:
            response = self.server.gateway.store.denial(exc)
        except (OSError, ValueError):
            return
        except Exception:
            # Database/audit failures must never forward work or report success.
            response = {"ok": False, "error": {"code": "unavailable", "retryable": False,
                        "rule": "gateway authority unavailable; operator intervention required"}}
        self.wfile.write((canonical(response) + "\n").encode())


def serve(store, github):
    # Lock before recovery: a second server must not mark live work uncertain.
    with (store.directory / "gateway.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store.verify()
        store.recover()
        address = store.directory / "gateway.sock"
        if address.exists():
            if not address.is_socket():
                raise ValueError("gateway.sock exists and is not a socket")
            address.unlink()
        with Server(str(address), Gateway(store, github)) as server:
            address.chmod(0o600)
            print("Gateway ready: " + str(address), flush=True)
            try:
                server.serve_forever()
            finally:
                address.unlink(missing_ok=True)


def client(address, request):
    data = (canonical(request) + "\n").encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError("request exceeds 1 MiB")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(180)
        connection.connect(str(address))
        connection.sendall(data)
        with connection.makefile("rb") as stream:
            data = stream.readline(2 * MAX_MESSAGE + 1)
        if len(data) > 2 * MAX_MESSAGE or not data.endswith(b"\n"):
            raise ValueError("invalid gateway response")
        return parse_json(data)
