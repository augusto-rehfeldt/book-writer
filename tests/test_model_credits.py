import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_book_creator.services import ai_service as api


class ModelCreditTests(unittest.TestCase):
    def test_every_model_that_answers_is_credited_once(self):
        from ai_book_creator.utils.ebook_exporter import ai_credits, load_provider_info

        client = object.__new__(api.AIService)
        client.provider_label, client.writing_model, client.review_model = 'openai-oauth', 'gpt-a', 'gpt-b'
        client._generate_content_once = lambda *a: 'text'
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {'AI_MODELS_USED_PATH': str(Path(folder) / 'models_used.json'),
                                        'AI_BOOK_COVER_SOURCE': 'pollinations'}):
            for kwargs in ({}, {}, {'model_type': 'review'}, {'model': 'judge-c'}):
                client.generate_content('p', **kwargs)
            self.assertEqual(ai_credits(load_provider_info(Path(folder))), [
                'OpenAI Codex (gpt-a)', 'OpenAI Codex (gpt-b)',
                'OpenAI Codex (judge-c)', 'Pollinations (cover image)'])



if __name__ == '__main__':
    unittest.main()
