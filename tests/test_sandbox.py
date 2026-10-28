import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_leases.gateway import Gateway, Server
from agent_leases.sandbox import command
from agent_leases.store import Store
from tests.fake_github import FakeGitHub
from tests.test_gateway import policy


class SandboxTests(unittest.TestCase):
    def test_unsupported_platform_fails_closed(self):
        with patch("agent_leases.sandbox.sys.platform", "darwin"):
            with self.assertRaisesRegex(ValueError, "no unsandboxed fallback"):
                command("/no-socket", "/tmp", ["sh"])

    def test_missing_bubblewrap_fails_closed(self):
        with patch("agent_leases.sandbox.sys.platform", "linux"), patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "install bubblewrap"):
                command("/no-socket", "/tmp", ["sh"])

    def test_linux_boundary(self):
        if sys.platform != "linux" or not shutil.which("bwrap"):
            if os.environ.get("LEASE_REQUIRE_SANDBOX"):
                self.fail("Linux and bubblewrap are required in CI")
            self.skipTest("Linux/bubblewrap integration runs in CI")
        with tempfile.TemporaryDirectory(prefix="al-") as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            secret = root / "host-secret"
            secret.write_text("HOST_CANARY")
            store = Store.create(root / "s", policy())
            endpoint = str(store.directory / "gateway.sock")
            remote = FakeGitHub()
            with Server(endpoint, Gateway(store, remote)) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    code = '''
import json, os, socket
from pathlib import Path
from agent_leases.gateway import client
assert 'HOST_TOKEN' not in os.environ
assert not Path(%r).exists()
assert not Path(%r).exists()
for family, kind, target in [
    (socket.AF_INET, socket.SOCK_STREAM, ('1.1.1.1', 443)),
    (socket.AF_INET, socket.SOCK_DGRAM, ('1.1.1.1', 53)),
    (socket.AF_INET6, socket.SOCK_STREAM, ('2606:4700:4700::1111', 443, 0, 0))]:
    s = socket.socket(family, kind)
    s.settimeout(1)
    try:
        if kind == socket.SOCK_DGRAM:
            s.sendto(b'probe', target)
        else:
            s.connect(target)
    except OSError:
        pass
    else:
        raise AssertionError('network escaped sandbox')
    finally:
        s.close()
try:
    socket.getaddrinfo('example.com', 443)
except OSError:
    pass
else:
    raise AssertionError('DNS escaped sandbox')
request = {'id': 'sandbox-read', 'operation': 'read_file', 'params': {'path': 'README.md'}}
assert client(os.environ['LEASE_SOCKET'], request)['ok']
for operation in ('grant', 'revoke', 'http'):
    result = client(os.environ['LEASE_SOCKET'], {'id': operation, 'operation': operation, 'params': {}})
    assert result['error']['code'] == 'unknown_operation'
Path('/workspace/result.txt').write_text('sandbox passed')
print('sandbox passed')
''' % (str(secret), str(store.database))
                    result = subprocess.run(command(endpoint, work, ["python3", "-c", code]),
                                            capture_output=True, text=True, close_fds=True,
                                            env={"HOST_TOKEN": "ENV_CANARY"}, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual((work / "result.txt").read_text(), "sandbox passed")
                    self.assertEqual(len(remote.calls), 1)
                    # A workspace socket is an alternate host boundary; refuse it.
                    with socket.socket(socket.AF_UNIX) as extra:
                        extra.bind(str(work / "escape.sock"))
                        with self.assertRaises(ValueError):
                            command(endpoint, work, ["true"])
                finally:
                    server.shutdown()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
