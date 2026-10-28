import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from agent_leases.cli import parser
from agent_leases.store import Store
from tests.test_adapters import ROOT, mcp_config, mcp_policy


class LaunchParsingTests(unittest.TestCase):
    def test_options_after_policy_and_worker_flags_are_preserved(self):
        args = parser().parse_args(['launch', 'policy.json', '--config', 'gateway.json',
                                   '--state', 'private', '--workspace', 'work', '--',
                                   'python3', '-c', 'print("hello")'])
        self.assertEqual(args.state, 'private')
        self.assertEqual(args.argv, ['python3', '-c', 'print("hello")'])


class LaunchTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'linux' or not shutil.which('bwrap'):
            if os.environ.get('LEASE_REQUIRE_SANDBOX'):
                self.fail('Linux and bubblewrap are required in CI')
            self.skipTest('full launcher integration runs on Linux in CI')
        self.temp = tempfile.TemporaryDirectory(prefix='al-')
        self.root = Path(self.temp.name)
        self.work, self.data, self.state = [self.root / name for name in ('work', 'data', 'state')]
        self.work.mkdir()
        self.data.mkdir()
        (self.work / 'worker.py').write_bytes((ROOT / 'examples/worker.py').read_bytes())
        self.policy = self.root / 'policy.json'
        self.policy.write_text(json.dumps(mcp_policy()))
        self.config = self.root / 'gateway.json'
        self.config.write_text(json.dumps(mcp_config(self.data)))
        self.config.chmod(0o600)
        self.worker = None

    def tearDown(self):
        if self.worker:
            if self.worker.poll() is None:
                self.worker.kill()
            self.worker.communicate()
        self.temp.cleanup()

    def start(self):
        self.worker = subprocess.Popen([sys.executable, '-m', 'agent_leases', 'launch', str(self.policy),
            '--state', str(self.state), '--config', str(self.config), '--workspace', str(self.work), '--yes',
            '--', 'python3', 'worker.py'], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def pending(self, request_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.worker.poll() is not None:
                out, err = self.worker.communicate()
                self.fail('launcher exited before request: ' + out + err)
            if (self.state / 'gateway.sock').is_socket():
                items = Store(self.state).inbox()
                if any(item['id'] == request_id and item['state'] == 'pending' for item in items):
                    return
            time.sleep(0.05)
        self.fail('worker did not submit ' + request_id)

    def operator(self, *args):
        result = subprocess.run([sys.executable, '-m', 'agent_leases', *args, '--state', str(self.state)],
                                cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_complete_launch_question_partial_budget_and_cleanup(self):
        self.start()
        self.pending('note-text')
        self.assertIn('note-text', self.operator('inbox'))
        self.operator('answer', 'note-text', 'Hello from the operator')
        self.pending('one-more')
        self.operator('decide', 'one-more', 'approve', '--amount', '1', '--yes')
        out, err = self.worker.communicate(timeout=15)
        self.assertEqual(self.worker.returncode, 0, out + err)
        notes = [json.loads(line) for line in (self.data / 'demo.jsonl').read_text().splitlines()]
        self.assertEqual(notes, [{'text': 'Hello from the operator'},
                                 {'text': 'The second note needed your approval.'}])
        status = Store(self.state).status()
        self.assertEqual(status['state'], 'revoked')
        self.assertEqual(status['used']['notes.add'], 2)
        self.assertFalse((self.state / 'gateway.sock').exists())
        self.assertEqual(len(Store(self.state).verify()), 64)

    def test_revoke_stops_waiting_worker_and_removes_gateway_socket(self):
        self.start()
        self.pending('note-text')
        self.operator('revoke')
        out, err = self.worker.communicate(timeout=10)
        self.assertNotEqual(self.worker.returncode, 0, out + err)
        self.assertFalse((self.data / 'demo.jsonl').exists())
        self.assertFalse((self.state / 'gateway.sock').exists())
        self.assertEqual(Store(self.state).inbox('note-text')['state'], 'closed')

    def test_denied_request_finishes_without_an_extra_write(self):
        self.start()
        self.pending('note-text')
        self.operator('answer', 'note-text', 'One note')
        self.pending('one-more')
        self.operator('decide', 'one-more', 'deny')
        out, err = self.worker.communicate(timeout=10)
        self.assertEqual(self.worker.returncode, 0, out + err)
        self.assertEqual(Store(self.state).status()['used']['notes.add'], 1)

    def test_interruption_revokes_and_cleans_up(self):
        self.start()
        self.pending('note-text')
        self.worker.terminate()
        out, err = self.worker.communicate(timeout=10)
        self.assertEqual(self.worker.returncode, 130, out + err)
        self.assertEqual(Store(self.state).status()['state'], 'revoked')
        self.assertFalse((self.state / 'gateway.sock').exists())

    def test_failed_worker_start_revokes_the_approved_lease(self):
        (self.work / 'worker.py').write_text('raise SystemExit(7)\n')
        self.start()
        out, err = self.worker.communicate(timeout=10)
        self.assertEqual(self.worker.returncode, 7, out + err)
        self.assertEqual(Store(self.state).status()['state'], 'revoked')
        self.assertFalse((self.state / 'gateway.sock').exists())

    def test_gateway_command_cannot_come_from_writable_worker_directory(self):
        self.config.write_text(json.dumps({'notes': {'command': ['/usr/bin/python3', str(self.work / 'worker.py')]}}))
        self.start()
        out, err = self.worker.communicate(timeout=10)
        self.assertNotEqual(self.worker.returncode, 0)
        self.assertIn('outside the workspace', out + err)
        self.assertFalse(self.state.exists())
