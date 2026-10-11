import unittest
from unittest.mock import Mock

from cloud_pc.host_program.llm_backends import CodexBackend, DshBackend, OpenCodeBackend, codex_item


class ProgressTests(unittest.TestCase):
    def test_codex_only_public_summary(self):
        self.assertEqual(codex_item({'type': 'reasoning', 'content': ['hidden'], 'encryptedContent': 'secret'}), [])
        self.assertEqual(codex_item({'type': 'reasoning', 'summary': ['公开摘要']})[0]['text'], '公开摘要')

    def test_codex_stream_and_session_isolation(self):
        b = CodexBackend({})
        b.observe('turn/started', {'threadId': 'a', 'turn': {'id': 't'}})
        for delta in ['先', '检查']:
            b.observe('item/reasoning/summaryTextDelta', {'threadId': 'a', 'turnId': 't',
                      'itemId': 'r', 'summaryIndex': 0, 'delta': delta})
        b.rpc = Mock(side_effect=[{'thread': {'status': {'type': 'active'}}},
            {'data': [{'id': 't', 'status': 'inProgress', 'items': []}]}])
        self.assertIn('先检查', b.current('a')['text'])
        b.rpc = Mock(side_effect=[{'thread': {}}, {'data': []}])
        self.assertEqual(b.current('b')['text'], '')

    def test_codex_completed_tool_authoritative(self):
        b = CodexBackend({})
        common = {'threadId': 'a', 'turnId': 't'}
        b.observe('item/started', dict(common, item={'id': 'c', 'type': 'commandExecution', 'status': 'inProgress'}))
        b.observe('item/completed', dict(common, item={'id': 'c', 'type': 'commandExecution',
                  'status': 'completed', 'exitCode': 0, 'aggregatedOutput': 'ok'}))
        text = codex_item(b.live_items['a']['items']['c'])[0]['text']
        self.assertIn('completed', text)
        self.assertIn('exit=0', text)

    def test_opencode_parts(self):
        b = OpenCodeBackend({'url': 'http://127.0.0.1:4096'})
        b.http = Mock(side_effect=[{}, [{'info': {'role': 'assistant'}, 'parts': [
            {'type': 'reasoning', 'text': '摘要'},
            {'type': 'tool', 'tool': 'read', 'state': {'status': 'completed', 'output': '结果'}},
            {'type': 'text', 'text': '回复'}]}], []])
        text = b.current('s')['text']
        for expected in ['思考摘要', '摘要', 'read', '结果', '回复']: self.assertIn(expected, text)

    def test_dsh_chunk_replaced_by_final(self):
        b = DshBackend({'url': 'http://127.0.0.1:3080'})
        events = [
            {'type': 'assistant/chunk', 'data': {'turn': 1, 'step': 1, 'chunk': {'type': 'block-start', 'index': 0, 'blockType': 'reasoning'}}},
            {'type': 'assistant/chunk', 'data': {'turn': 1, 'step': 1, 'chunk': {'type': 'text-delta', 'index': 0, 'text': '摘要'}}}]
        def read():
            b.rpc = Mock(side_effect=[{'items': [{'sessionId': 's', 'running': True}]},
                                     {'events': [{'event': e} for e in events]}])
            return b.current('s')['text']
        self.assertIn('摘要', read())
        events.append({'type': 'assistant/message', 'data': {'turn': 1, 'step': 1, 'message': {
            'content': [{'type': 'reasoning', 'text': '摘要'}, {'type': 'text', 'text': '回复'}]}}})
        text = read()
        self.assertEqual(text.count('思考摘要:'), 1)
        self.assertIn('回复', text)
