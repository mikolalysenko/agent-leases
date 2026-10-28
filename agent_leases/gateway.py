"""One policy boundary for every adapter. Administrative operations are local only."""

from contextlib import contextmanager
import fcntl
import socket
import socketserver
import threading

from .adapters import binding, catalog, connect
from .policy import (Denied, UpstreamError, MAX_MESSAGE, canonical, digest, fields, parse_json,
                     validate_request)


class Context:
    def __init__(self, store, request, service):
        self.store, self.request, self.service = store, request, service
        self.lease = store.status()

    def dispatch(self, method, resource):
        self.store.dispatch(self.request["id"], method, resource)


class Gateway:
    def __init__(self, store, config):
        self.store = store
        self.adapters = connect(store.status()["policy"], config)
        self.lock = threading.Lock()

    def close(self):
        for adapter in self.adapters.values():
            adapter.close()

    def handle(self, request):
        try:
            if isinstance(request, dict):
                control = request.get("operation")
                if control in ("status", "catalog"):
                    fields(request, ("operation",))
                    result = self.store.status()
                    if control == "catalog":
                        result = catalog(result["policy"])
                    return {"ok": True, "result": result}
                if control == "submit":
                    fields(request, ("operation", "request"))
                    return {"ok": True, "result": self.store.submit(request["request"])}
                if control == "poll":
                    fields(request, ("operation", "request_id"))
                    return {"ok": True, "result": self.store.inbox(request["request_id"])}
            validate_request(request, self.store.status()["policy"])
            with self.lock:
                cached = self.store.reserve(request)
                if cached is not None:
                    return cached
                try:
                    result, owned = self.execute(request)
                    if len(canonical(result).encode()) > MAX_MESSAGE:
                        raise ValueError("adapter response is too large")
                    response = {"ok": True, "result": result}
                except Denied as exc:
                    response, owned = self.store.denial(exc), None
                except UpstreamError as exc:
                    error = Denied("upstream" if exc.status else "uncertain",
                                   str(exc) if exc.status else "connection failed; inspect the service before new work")
                    response, owned = self.store.denial(error), None
                except Exception:
                    response, owned = self.store.denial(Denied(
                        "uncertain", "upstream operation failed; inspect the service before new work")), None
                self.store.finish(request, response, owned)
                return response
        except Denied as exc:
            return self.store.denial(exc)

    def execute(self, request):
        spec = binding(self.store.status()["policy"], request["operation"])
        context = Context(self.store, request, spec["service"])
        # The core authorizes entry; adapters also check before each external effect.
        context.dispatch("adapter", request["operation"])
        return self.adapters[spec["service"]].execute(context, spec["local_name"], request["params"])


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


@contextmanager
def serving(store, config):
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
        gateway = Gateway(store, config)
        try:
            with Server(str(address), gateway) as server:
                address.chmod(0o600)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    yield address
                finally:
                    server.shutdown()
                    thread.join()
        finally:
            gateway.close()
            address.unlink(missing_ok=True)


def serve(store, config):
    with serving(store, config) as address:
        print("Gateway ready: " + str(address), flush=True)
        threading.Event().wait()


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


def operation_request(operation, params, request_id=None):
    """Identical commands safely reuse their result unless an explicit ID is supplied."""
    request = {"operation": operation, "params": params}
    request["id"] = request_id if request_id is not None else digest(request)
    return validate_request(request)
