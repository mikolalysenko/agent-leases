import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

from agent_leases.gateway import serving
from agent_leases.sandbox import command
from agent_leases.store import Store
from tests.fake_github import FakeGitHub
from tests.test_gateway import policy


class NetworkClientsTests(unittest.TestCase):
    def test_gh_http_and_mcp_subprocess_cannot_bypass_gateway(self):
        needed = sys.platform == 'linux' and all(shutil.which(tool) for tool in ('bwrap', 'gh', 'curl'))
        if not needed:
            if os.environ.get('LEASE_REQUIRE_SANDBOX'):
                self.fail('Linux, bubblewrap, gh, and curl are required in CI')
            self.skipTest('network client integration runs on Linux in CI')
        observations = []
        class Proxy(http.server.BaseHTTPRequestHandler):
            def do_CONNECT(self):
                observations.append(self.path)
                self.send_error(502, 'Controlled test endpoint; no forwarding')
            def do_GET(self):
                observations.append('GET ' + self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'Controlled test endpoint')
            def log_message(self, *args):
                pass
        with http.server.ThreadingHTTPServer(('127.0.0.1', 0), Proxy) as receiver:
            thread = threading.Thread(target=receiver.serve_forever, daemon=True)
            thread.start()
            try:
                proxy = 'http://127.0.0.1:%d' % receiver.server_port
                env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'GH_TOKEN': 'FAKE_TEST_CREDENTIAL',
                       'HTTPS_PROXY': proxy, 'HTTP_PROXY': proxy, 'NO_PROXY': ''}
                # Positive control proves the exact gh invocation uses our receiver.
                subprocess.run(['gh', 'api', 'repos/mikolalysenko/agent-leases'], env=env,
                               capture_output=True, timeout=15)
                self.assertEqual(observations, ['api.github.com:443'])
                subprocess.run(['curl', '--max-time', '3', '--fail', 'https://github.com'], env=env,
                               capture_output=True, timeout=5)
                self.assertEqual(observations[-1], 'github.com:443')
                with urllib.request.urlopen(proxy, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(observations[-1], 'GET /')
                control = list(observations)
                with tempfile.TemporaryDirectory(prefix='al-') as temp:
                    root = Path(temp)
                    work = root / 'work'
                    work.mkdir()
                    # An untrusted MCP server started by the worker has the same boundary.
                    (work / 'network_mcp.py').write_text('''
import json, sys, urllib.request
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg: continue
    if msg['method'] == 'initialize':
        result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'probe', 'version': '1'}}
    else:
        try:
            urllib.request.urlopen(sys.argv[1], timeout=2)
        except OSError:
            result = {'blocked': True}
        else:
            result = {'blocked': False}
    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}), flush=True)
''')
                    code = '''
import json, os, subprocess
from agent_leases.gateway import client, operation_request
env = {**os.environ, **%r}
for argv in (['gh', 'api', 'repos/mikolalysenko/agent-leases'],
             ['curl', '--max-time', '3', '--fail', 'https://github.com']):
    result = subprocess.run(argv, env=env, capture_output=True, timeout=5)
    assert result.returncode != 0, (argv, result.stdout)
messages = [
    {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'network'}}]
result = subprocess.run(['python3', 'network_mcp.py', %r],
    input=''.join(json.dumps(m) + '\\n' for m in messages), text=True,
    capture_output=True, timeout=5)
assert result.returncode == 0, result.stderr
assert json.loads(result.stdout.splitlines()[-1])['result']['blocked']
assert client(os.environ['LEASE_SOCKET'], operation_request('read_file', {'path': 'README.md'}))['ok']
print('all direct routes blocked; gateway read succeeded')
''' % (env, proxy)
                    store = Store.create(root / 's', policy())
                    upstream = FakeGitHub()
                    with serving(store, upstream) as endpoint:
                        result = subprocess.run(command(endpoint, work, ['python3', '-c', code]),
                                                capture_output=True, text=True, timeout=20)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(len(upstream.calls), 1)
                self.assertEqual(observations, control)
            finally:
                receiver.shutdown()
                thread.join()
