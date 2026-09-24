"""Offline ledger crash/concurrency and shared-client contract checks."""
import json
import multiprocessing
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_book_creator.services import ai_service as api


def service(folder):
    config = Path(folder) / 'config.json'
    return api.AIService(str(config), str(Path(folder) / 'usage.json'),
                         allow_auth_prompt=False, client_max_retries=0)


def record_many(folder):
    client = service(folder)
    for _ in range(10):
        client._record_openai_usage('mini', {'total_tokens': 3})


class ServiceContractTests(unittest.TestCase):
    def test_concurrent_writers_and_corrupt_state_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'config.json').write_text(json.dumps({'provider': 'openai', 'api_key': 'fixture',
                'groq_rate_state_path': str(path / 'groq.json')}))
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(record_many, [folder] * 3))
            workers = [multiprocessing.get_context('spawn').Process(target=record_many, args=(folder,)) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers:
                worker.join(30)
                if worker.is_alive():
                    worker.terminate()
                    worker.join()
                self.assertEqual(worker.exitcode, 0)
            client = service(folder)
            self.assertEqual(client.get_budget_status()['buckets']['mini']['tokens'], 150)
            before = (path / 'usage.json').read_bytes()
            with patch.object(api.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(api.UsageStateError):
                    client._record_openai_usage('mini', {'total_tokens': 5})
            self.assertEqual((path / 'usage.json').read_bytes(), before)
            (path / 'usage.json').write_text('{broken')
            with self.assertRaises(api.UsageStateError): service(folder)
            self.assertEqual((path / 'usage.json').read_text(), '{broken')

    def test_public_options_reach_responses_and_do_not_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'config.json').write_text(json.dumps({'provider': 'openai', 'api_key': 'fixture',
                'groq_rate_state_path': str(path / 'groq.json')}))
            client = service(folder)
            client.set_reasoning_effort('high', 'low')
            client.client = Mock()
            client.client.responses.create.return_value = SimpleNamespace(output_text='answer',
                usage={'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3})
            self.assertEqual(client.generate_content('question', model_type='review', max_completion_tokens=50, max_retries=1), 'answer')
            sent = client.client.responses.create.call_args.kwargs
            self.assertEqual(sent['reasoning'], {'effort': 'low'})
            self.assertEqual(sent['max_output_tokens'], 50)
            requests_before = client.client.responses.create.call_count
            with patch.object(api.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(api.UsageStateError):
                    client.generate_content('question', max_retries=3)
            self.assertEqual(client.client.responses.create.call_count, requests_before + 1)
            with patch('builtins.input', side_effect=AssertionError('must not prompt')):
                self.assertFalse(client._handle_401_auth_error())
            with patch.object(api, 'OpenAI') as constructor:
                api.AIService._init_client(client)
                self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)
            client.provider = 'openai-oauth'
            client.client = Mock()
            client.client.chat.completions.create.return_value = {'choices': [{'message': {'content': 'answer'}}]}
            client.generate_content('question', model_type='review')
            self.assertEqual(client.client.chat.completions.create.call_args.kwargs['extra_body'], {'reasoning': {'effort': 'low'}})


if __name__ == '__main__':
    unittest.main()
