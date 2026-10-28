import json
import unittest
from unittest.mock import patch

from agent_leases.cli import parser, worker_request
from agent_leases.gateway import operation_request
from agent_leases.mcp import Session
from agent_leases.policy import Denied, starter_policy


class InterfaceTests(unittest.TestCase):
    def initialize(self, session):
        response = session.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}, '_meta': {}}})
        self.assertEqual(response['result']['protocolVersion'], '2025-11-25')
        self.assertIsNone(session.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))

    def test_friendly_and_json_commands_share_the_same_request(self):
        friendly = worker_request(parser().parse_args(['read', 'README.md', '--json']))
        raw = worker_request(parser().parse_args(['call', 'read_file', '--params', '{"path":"README.md"}']))
        self.assertEqual(friendly, raw)
        self.assertNotEqual(friendly, worker_request(parser().parse_args(['read', 'README.md', '--id', 'new'])) )

    def test_starter_policy_is_ready_for_approval(self):
        policy = starter_policy('owner/repo', 'a' * 40)
        self.assertEqual(policy['capabilities']['publish'], 1)
        self.assertEqual(policy['ttl_seconds'], 3600)
        with self.assertRaises(Denied):
            starter_policy('owner/repo', 'main')

    def test_mcp_does_not_invoke_tools_before_initialization_or_from_notifications(self):
        session = Session('/unused')
        request = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'lease_read_file', 'arguments': {'path': 'README.md'}}}
        with patch('agent_leases.mcp.client') as client:
            self.assertIn('error', session.handle(request))
            self.initialize(session)
            del request['id']
            self.assertIsNone(session.handle(request))
            client.assert_not_called()

    def test_mcp_forwards_only_worker_calls_and_returns_the_structured_denial(self):
        session = Session('/unused')
        self.initialize(session)
        denial = {'ok': False, 'error': {'code': 'limit', 'rule': 'read_file exhausted', 'retryable': False}}
        request = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'lease_read_file', 'arguments': {'path': 'README.md'}, '_meta': {'progressToken': 'x'}}}
        with patch('agent_leases.mcp.client', return_value=denial) as client:
            response = session.handle(request)['result']
            self.assertEqual(client.call_args.args[1], operation_request('read_file', {'path': 'README.md'}))
            self.assertTrue(response['isError'])
            self.assertEqual(response['structuredContent'], denial)
            self.assertEqual(json.loads(response['content'][0]['text']), denial)
            request['params']['name'] = 'lease_grant'
            self.assertIn('error', session.handle(request))
            self.assertEqual(client.call_count, 1)

    def test_mcp_malformed_envelopes_do_not_crash_or_invoke(self):
        session = Session('/unused')
        with patch('agent_leases.mcp.client') as client:
            for request in (None, [], {}, {'jsonrpc': '2.0', 'id': {}, 'method': 'ping'}):
                self.assertIn('error', session.handle(request))
            client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
