import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_leases.adapters import catalog
from agent_leases.gateway import Gateway, client, operation_request, serving
from agent_leases.mcp import Session
from agent_leases.policy import Denied, compile_policy, fields, render
from agent_leases.store import Store
from tests.fake_github import FakeGitHub

ROOT = Path(__file__).resolve().parents[1]


def mcp_policy():
    return json.loads((ROOT / 'examples/mcp-lease.json').read_text())


def mcp_config(directory):
    return {'notes': {'command': [sys.executable, str(ROOT / 'examples/notes_server.py'), str(directory)]}}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='al-')
        self.root = Path(self.temp.name)
        self.store = Store.create(self.root / 's', mcp_policy())
        self.gateway = Gateway(self.store, mcp_config(self.root))

    def tearDown(self):
        self.gateway.close()
        self.temp.cleanup()

    def call(self, name, params, key=None):
        return self.gateway.handle(operation_request(name, params, key))

    def notes(self):
        path = self.root / 'demo.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_mcp_tool_resource_and_prompt_share_core_budgets(self):
        self.assertTrue(self.call('notes.guide', {})['ok'])
        prompt = self.call('notes.prompt', {'topic': 'budgets'})
        self.assertIn('budgets', prompt['result']['messages'][0]['content']['text'])
        first = self.call('notes.add', {'text': 'one'}, 'first')
        self.assertTrue(first['ok'], first)
        self.assertEqual(self.call('notes.add', {'text': 'one'}, 'first'), first)
        self.assertEqual(self.call('notes.add', {'text': 'two'}, 'second')['error']['code'], 'limit')
        self.assertEqual(self.notes(), [{'text': 'one'}])
        self.assertEqual(self.store.status()['used']['operations'], 3)
        self.assertEqual(self.store.status()['used']['write_operations'], 1)

    def test_fixed_fields_and_unknown_methods_cannot_be_overridden(self):
        for name, params in (
            ('notes.add', {'text': 'leak', 'notebook': 'attacker'}),
            ('notes.guide', {'uri': 'file:///etc/passwd'}),
            ('notes.delete', {}), ('tools/call', {'name': 'delete'}),
        ):
            self.assertFalse(self.gateway.handle({'id': 'bad', 'operation': name, 'params': params})['ok'])
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.store.status()['used']['operations'], 0)

    def test_top_level_input_cannot_replace_fixed_method_parameters(self):
        policy = mcp_policy()
        spec = policy['services']['notes']['operations']['add']
        spec['input'] = ''
        store = Store.create(self.root / 'top', policy)
        gateway = Gateway(store, mcp_config(self.root))
        try:
            result = gateway.handle(operation_request('notes.add', {'name': 'different'}))
            self.assertEqual(result['error']['code'], 'scope')
            self.assertEqual(self.notes(), [])
        finally:
            gateway.close()

    def test_gateway_mcp_discovers_adapters_and_carries_control_requests(self):
        session = Session('/unused')
        session.initialized = session.ready = True
        with patch('agent_leases.mcp.client', side_effect=lambda _, body: self.gateway.handle(body)):
            listing = session.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
            names = {tool['name'] for tool in listing}
            self.assertEqual(names, {'lease_status', 'lease_ask', 'lease_request', 'lease_poll',
                                     'lease_notes.add', 'lease_notes.guide', 'lease_notes.prompt'})
            def tool(name, arguments):
                return session.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                       'params': {'name': name, 'arguments': arguments}})
            result = tool('lease_notes.add', {'params': {'text': 'via MCP', 'request_id': 'upstream-field'},
                                             'request_id': 'lease-id'})['result']
            self.assertFalse(result['isError'])
            self.assertEqual(self.notes(), [{'text': 'via MCP'}])
            answer = tool('lease_request', {'cap': 'notes.add', 'amount': 1, 'message': 'one more',
                                            'request_id': 'more'})['result']['structuredContent']
            self.assertEqual(answer['result']['state'], 'pending')
            self.assertIn('error', tool('lease_grant', {'cap': 'notes.add', 'amount': 99}))
            self.assertEqual(self.store.status()['policy']['capabilities']['notes.add'], 1)

    def test_config_is_operator_only_and_never_in_catalog_or_status(self):
        config = mcp_config(self.root)
        config['notes']['env'] = {'CANARY_CREDENTIAL': 'host-only-value'}
        gateway = Gateway(self.store, config)
        try:
            for op in ('status', 'catalog'):
                result = gateway.handle({'operation': op})
                self.assertNotIn('host-only-value', json.dumps(result))
                self.assertNotIn(str(ROOT), json.dumps(result))
        finally:
            gateway.close()

    def test_uncertain_mcp_outcome_is_charged_and_not_replayed(self):
        self.call('notes.add', {'text': 'one'})
        remote = self.gateway.adapters['notes'].remote
        # A real server process has been started; kill it before the next dispatch.
        remote.process.kill()
        remote.process.wait()
        failed = self.call('notes.guide', {}, 'after-crash')
        self.assertEqual(failed['error']['code'], 'uncertain')
        self.assertEqual(self.call('notes.guide', {}, 'after-crash'), failed)
        self.assertEqual(self.store.status()['used']['notes.guide'], 1)
        self.assertTrue(remote.broken)

    def test_public_policy_cannot_supply_commands_credentials_or_uninstalled_plugins(self):
        for edit in ('command', 'token', 'adapter', 'notification', 'write'):
            policy = mcp_policy()
            options = policy['services']['notes']
            if edit in ('command', 'token'):
                options[edit] = 'untrusted'
            elif edit == 'adapter':
                options['adapter'] = 'uninstalled_adapter_xyz'
            elif edit == 'notification':
                options['operations']['add']['method'] = 'notifications/initialized'
            else:
                options['operations']['add']['write'] = 0
            with self.subTest(edit=edit), self.assertRaises(Denied):
                compile_policy(policy)

    def test_approval_renders_mcp_fixed_values_input_scope_and_caps(self):
        output = render(mcp_policy())
        for text in ('notes.add: at most 1', 'tools/call', 'notebook', 'demo',
                     'other fields', 'unrestricted', 'declared read-only', 'resources/read'):
            self.assertIn(text, output)

    def test_installed_plugin_needs_no_gateway_changes_and_composes_with_mcp(self):
        effects = []
        class Plugin:
            @staticmethod
            def compile(options):
                fields(options, ('bucket',))
            @staticmethod
            def operations(options):
                return {'put': {'description': 'Put a value in ' + options['bucket'], 'write': True,
                                'inputSchema': {'type': 'object'}}}
            @staticmethod
            def validate(options, operation, params):
                fields(params, ('value',))
            @staticmethod
            def describe(options):
                return ['Bucket: ' + options['bucket']]
            def __init__(self, options, config):
                self.options = options
            def execute(self, context, operation, params):
                context.dispatch('put', self.options['bucket'])
                effects.append((self.options['bucket'], params['value']))
                return {'saved': True}, None
            def close(self):
                pass
        class Entry:
            name = 'example'
            def load(self):
                return Plugin
        policy = mcp_policy()
        policy['services']['other'] = {'adapter': 'example', 'bucket': 'approved'}
        policy['capabilities']['other.put'] = 1
        with patch('agent_leases.adapters.entry_points', return_value={'agent_leases.adapters': [Entry()]}):
            store = Store.create(self.root / 'plugin', policy)
            config = {**mcp_config(self.root), 'other': {}}
            with serving(store, config) as address:
                self.assertIn('other.put', catalog(policy))
                self.assertTrue(client(address, operation_request('other.put', {'value': 'one'}))['ok'])
                self.assertFalse(client(address, operation_request('other.put', {'value': 'two'}))['ok'])
                self.assertTrue(client(address, operation_request('notes.guide', {}))['ok'])
                store.revoke()
                self.assertFalse(client(address, operation_request('notes.add', {'text': 'no'}))['ok'])
            self.assertFalse(address.exists())
        self.assertEqual(effects, [('approved', 'one')])

    def test_github_resource_ownership_is_scoped_to_each_service(self):
        policy = mcp_policy()
        scope = {'adapter': 'github', 'repo': 'owner/repo', 'base': 'main', 'base_sha': 'a' * 40}
        policy['services'].update(first=scope, second=scope)
        policy['capabilities'].update({'first.publish': 1, 'first.open_pr': 1, 'second.open_pr': 1})
        store = Store.create(self.root / 'github', policy)
        remote = FakeGitHub()
        with patch('agent_leases.adapters.github.GitHub', return_value=remote):
            gateway = Gateway(store, {**mcp_config(self.root), 'first': {'token_file': 'unused'},
                                      'second': {'token_file': 'unused'}})
        try:
            result = gateway.handle(operation_request('first.publish', {'message': 'x', 'files': {'x': 'x'}}))
            branch = result['result']['branch']
            params = {'branch': branch, 'title': 'x', 'body': ''}
            before = len(remote.calls)
            self.assertEqual(gateway.handle(operation_request('second.open_pr', params))['error']['code'], 'scope')
            self.assertEqual(len(remote.calls), before)
            self.assertTrue(gateway.handle(operation_request('first.open_pr', params))['ok'])
        finally:
            gateway.close()
