import unittest
from unittest.mock import Mock

from cloud_pc.host_program.llm_backends import CodexBackend, OpenCodeBackend, dsh_run_status


class RunStatusTests(unittest.TestCase):
    def dsh(self, entries, busy=True):
        return dsh_run_status([{'event': {'type': kind, 'data': data}} for kind, data in entries], busy, True)['state']

    def test_dsh_retry_recovery(self):
        events = [('turn/start', {}), ('llm/retry', {'retry': 2})]
        self.assertEqual(self.dsh(events), 'retrying')
        self.assertEqual(self.dsh(events + [('llm/retry-started', {})]), 'working')

    def test_dsh_approval_and_tool_pairing(self):
        events = [('tool/call', {'callId': 'a'}), ('approval/asked', {'id': 'q'})]
        self.assertEqual(self.dsh(events), 'waiting_approval')
        events += [('approval/decided', {'id': 'q'})]
        self.assertEqual(self.dsh(events), 'executing')
        events += [('tool/result', {'message': {'source': {'callId': 'a'}}})]
        self.assertEqual(self.dsh(events), 'working')

    def test_dsh_terminal_and_new_turn(self):
        for reason, state in [('aborted', 'stopped'), ('error', 'failed'), ('completed', 'completed')]:
            events = [('turn/end', {'reason': {'kind': reason}})]
            self.assertEqual(self.dsh(events, False), state)
            self.assertEqual(self.dsh(events + [('turn/start', {})]), 'working')

    def test_codex_lifecycle_session_isolation(self):
        b = CodexBackend({})
        b.observe('turn/started', {'threadId': 's', 'turn': {'id': 't'}})
        b.observe('error', {'threadId': 's', 'turnId': 't', 'willRetry': True})
        self.assertEqual(b.run_states['s']['state'], 'retrying')
        b.observe('item/started', {'threadId': 's', 'turnId': 't',
                                 'item': {'id': 'c', 'type': 'commandExecution'}})
        self.assertEqual(b.run_states['s']['state'], 'executing')
        b.observe('turn/completed', {'threadId': 's', 'turn': {'id': 't', 'status': 'interrupted'}})
        self.assertEqual(b.run_states['s']['state'], 'stopped')
        self.assertNotIn('other', b.run_states)

    def test_codex_approval_flag(self):
        b = CodexBackend({})
        b.rpc = Mock(side_effect=[{'thread': {'status': {'type': 'active', 'activeFlags': ['waitingOnApproval']}}}, {'data': []}])
        self.assertEqual(b.current('s')['run_status']['state'], 'waiting_approval')

    def test_opencode_retry_permission_and_abort(self):
        b = OpenCodeBackend({'url': 'http://127.0.0.1:4096'})
        b.http = Mock(side_effect=[{'s': {'type': 'retry', 'attempt': 3}}, [], []])
        result = b.current('s')
        self.assertEqual(result['run_status']['state'], 'retrying')
        self.assertEqual(result['run_status']['attempt'], 3)
        b.http = Mock(side_effect=[{'s': {'type': 'busy'}}, [], [{'sessionID': 's'}]])
        self.assertEqual(b.current('s')['run_status']['state'], 'waiting_approval')
        b.http = Mock(side_effect=[{}, [{'info': {'error': {'name': 'MessageAbortedError'}}, 'parts': []}], []])
        self.assertEqual(b.current('s')['run_status']['state'], 'stopped')

    def test_opencode_tool_finishes_and_other_session_permission(self):
        b = OpenCodeBackend({'url': 'http://127.0.0.1:4096'})
        for tool_state, expected in [('running', 'executing'), ('completed', 'working')]:
            b.http = Mock(side_effect=[{'s': {'type': 'busy'}}, [{'parts': [
                {'type': 'tool', 'state': {'status': tool_state}}]}], [{'sessionID': 'other'}]])
            self.assertEqual(b.current('s')['run_status']['state'], expected)
