import json
from pathlib import Path
import sys
import tempfile
import unittest

from agent_leases.gateway import Gateway, operation_request
from agent_leases.store import Store
from tests.test_adapters import mcp_policy


SERVER = '''
import json, sys
from pathlib import Path
mode, output = sys.argv[1:]
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request: continue
    if request['method'] == 'initialize':
        result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'adversarial-test', 'version': '1'}}
    else:
        with Path(output).open('a') as log:
            log.write(json.dumps({'effect': request['params']}) + '\\n')
        if mode == 'malformed':
            print('{bad json', flush=True)
            continue
        print(json.dumps({'jsonrpc': '2.0', 'id': 'callback', 'method': 'sampling/createMessage',
                          'params': {'messages': []}}), flush=True)
        answer = json.loads(sys.stdin.readline())
        with Path(output).open('a') as log:
            log.write(json.dumps({'callback_answer': answer}) + '\\n')
        result = {'content': [{'type': 'text', 'text': 'done'}]}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
'''


class StdioTests(unittest.TestCase):
    def run_case(self, mode):
        with tempfile.TemporaryDirectory(prefix='al-') as temp:
            root = Path(temp)
            server, output = root / 'server.py', root / 'effects.jsonl'
            server.write_text(SERVER)
            store = Store.create(root / 'state', mcp_policy())
            gateway = Gateway(store, {'notes': {'command': [sys.executable, str(server), mode, str(output)]}})
            try:
                request = operation_request('notes.add', {'text': 'one'})
                first = gateway.handle(request)
                self.assertEqual(gateway.handle(request), first)
                self.assertEqual(store.status()['used']['notes.add'], 1)
                records = [json.loads(line) for line in output.read_text().splitlines()]
                self.assertEqual(sum('effect' in record for record in records), 1)
                self.assertEqual(store.inbox(), [])
                return first, records
            finally:
                gateway.close()

    def test_malformed_response_after_effect_is_uncertain_and_never_replayed(self):
        response, records = self.run_case('malformed')
        self.assertEqual(response['error']['code'], 'uncertain')
        self.assertEqual(len(records), 1)

    def test_upstream_callbacks_cannot_invoke_worker_or_operator_actions(self):
        response, records = self.run_case('callback')
        self.assertTrue(response['ok'])
        self.assertEqual(records[1]['callback_answer']['error']['code'], -32601)
        self.assertEqual(records[1]['callback_answer']['id'], 'callback')
