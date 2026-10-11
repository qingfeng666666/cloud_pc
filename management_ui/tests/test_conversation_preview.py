import unittest
from cloud_pc.management_ui.conversation_preview import compact_conversation


class PreviewTests(unittest.TestCase):
    def test_messages_fold_independently_and_expand_losslessly(self):
        text = 'user:\nhello\n\nassistant:\n' + '\n'.join(str(i) for i in range(20)) + '\n\n工具结果:\nshort'
        preview = compact_conversation(text)
        self.assertIn('user:\nhello', preview)
        self.assertIn('…', preview)
        self.assertNotIn('\n19\n', preview)
        self.assertIn('工具结果:\nshort', preview)
        self.assertEqual(compact_conversation(text, True), text)

    def test_single_long_line_and_short_text(self):
        self.assertIn('…', compact_conversation('assistant:\n' + '字' * 1000))
        text = 'assistant:\nshort answer'
        self.assertEqual(compact_conversation(text), text)
