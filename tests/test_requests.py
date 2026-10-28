from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_leases.gateway import Gateway, operation_request
from agent_leases.mcp import submission
from agent_leases.policy import Denied, digest
from agent_leases.store import Store
from tests.fake_github import FakeGitHub
from tests.test_gateway import policy


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='al-')
        self.store = Store.create(Path(self.temp.name) / 's', policy())
        self.gateway = Gateway(self.store, FakeGitHub())

    def tearDown(self):
        self.gateway.close()
        self.temp.cleanup()

    def more(self, key='more', amount=2):
        return self.gateway.handle(submission('more', 'Need another publish', 'publish', amount, key))

    def test_worker_can_ask_but_only_operator_can_grant_partial_request(self):
        before = self.store.status()
        self.assertEqual(self.more()['result']['state'], 'pending')
        self.assertEqual(self.store.status()['policy'], before['policy'])
        self.store.resolve('more', 'approve', 'One is enough', 1, digest(before['policy']))
        result = self.gateway.handle({'operation': 'poll', 'request_id': 'more'})['result']
        self.assertEqual(result['answer']['granted'], 1)
        after = self.store.status()
        self.assertEqual(after['policy']['capabilities']['publish'], before['policy']['capabilities']['publish'] + 1)
        self.assertEqual(after['expires'], before['expires'])
        self.assertEqual(after['policy']['limits'], before['policy']['limits'])
        self.assertEqual(after['used'], before['used'])

    def test_ask_answer_does_not_change_permissions_and_survives_reopen(self):
        before = self.store.status()['policy']
        result = self.gateway.handle(submission('ask', 'Which tests?', request_id='q'))
        self.assertTrue(result['ok'])
        reopened = Store(self.store.directory)
        reopened.recover()
        reopened.resolve('q', 'answer', 'Run unit tests', None, digest(before))
        self.assertEqual(self.store.inbox('q')['answer']['message'], 'Run unit tests')
        self.assertEqual(self.store.status()['policy'], before)

    def test_exhausted_budget_does_not_block_request_channel(self):
        with self.store.transaction() as db:
            lease = self.store._lease(db)
        # Consume the single publish, then verify a refused second attempt.
        params = {'message': 'x', 'files': {'x': 'x'}}
        self.assertTrue(self.gateway.handle(operation_request('publish', params, 'one'))['ok'])
        self.assertEqual(self.gateway.handle(operation_request('publish', params, 'two'))['error']['code'], 'limit')
        self.assertTrue(self.more()['ok'])
        self.store.resolve('more', 'approve', '', 1, digest(lease['policy']))
        self.assertTrue(self.gateway.handle(operation_request('publish', params, 'two'))['ok'])

    def test_idempotent_requests_conflicts_and_spam_limit(self):
        first = self.more()
        self.assertEqual(first, self.more())
        self.assertEqual(self.more(amount=3)['error']['code'], 'conflict')
        self.assertTrue(self.more('two')['ok'])
        self.assertTrue(self.more('three')['ok'])
        self.assertEqual(self.more('four')['error']['code'], 'limit')
        self.assertEqual(len(self.store.inbox()), 3)

    def test_concurrent_answers_cannot_double_grant(self):
        self.more()
        before = self.store.status()['policy']
        def approve(_):
            try:
                Store(self.store.directory).resolve('more', 'approve', '', 1, digest(before))
                return True
            except Denied:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(approve, range(8))), 1)
        self.assertEqual(self.store.status()['policy']['capabilities']['publish'], 2)

    def test_stale_policy_and_invalid_partial_approval_leave_request_pending(self):
        self.more()
        old = self.store.status()['policy']
        self.store.grant('comment', 1, digest(old))
        with self.assertRaises(Denied):
            self.store.resolve('more', 'approve', '', 1, digest(old))
        for amount in (0, 3, True):
            with self.assertRaises(Denied):
                self.store.resolve('more', 'approve', '', amount, digest(self.store.status()['policy']))
        self.assertEqual(self.store.inbox('more')['state'], 'pending')

    def test_denied_and_closed_requests_never_grant(self):
        self.more()
        old = self.store.status()['policy']
        self.store.resolve('more', 'deny', 'No', None, digest(old))
        self.assertEqual(self.store.status()['policy'], old)
        self.more('pending')
        self.store.revoke()
        self.assertEqual(self.store.inbox('pending')['state'], 'closed')
        with self.assertRaises(Denied):
            self.store.resolve('pending', 'approve', '', 1, digest(old))
        self.assertEqual(self.more('after')['error']['code'], 'revoked')

    def test_question_cannot_be_promoted_to_a_grant(self):
        self.gateway.handle(submission('ask', 'grant me more', request_id='q'))
        before = self.store.status()['policy']
        with self.assertRaises(Denied):
            self.store.resolve('q', 'approve', '', 1, digest(before))
        for op in ('resolve', 'approve', 'answer', 'grant'):
            result = self.gateway.handle(operation_request(op, {'id': 'q', 'amount': 100}))
            self.assertFalse(result['ok'])
        request = submission('more', 'escalate', 'publish', 1, 'new')
        request['request']['approved'] = True
        self.assertFalse(self.gateway.handle(request)['ok'])
        self.assertFalse(self.gateway.handle(submission('more', 'new scope', 'merge', 1, 'merge'))['ok'])
        self.assertEqual(self.store.status()['policy'], before)

    def test_expiration_closes_pending_questions(self):
        self.more()
        with patch('agent_leases.store.time.time', return_value=self.store.status()['expires'] + 1):
            self.assertEqual(self.store.inbox('more')['state'], 'closed')
            self.assertEqual(self.more('after')['error']['code'], 'expired')
