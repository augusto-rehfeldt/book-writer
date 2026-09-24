"""Offline ledger crash/concurrency and shared-client contract checks."""
import json
import multiprocessing
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
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
            # no catalogue for this model: a caller's cap is only a floor under the role default
            self.assertEqual(sent['max_output_tokens'], client._default_completion_tokens('review'))
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

    def test_usage_limit_is_waited_out_never_returned(self):
        client = object.__new__(api.AIService)
        client.provider_label = 'claude'
        replies = ["You've hit your session limit"] * 4 + [RuntimeError('HTTP 429 Too Many Requests'), 'chapter text']

        def once(*args):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        client._generate_content_once = once
        with patch.object(api.time, 'sleep') as sleep:
            self.assertEqual(client.generate_content('p'), 'chapter text')
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [api.LIMIT_RETRY] * 4 + [api.LIMIT_PAUSE])

        # prose that merely mentions a limit, other errors and metered budget stops pass through
        client._generate_content_once = lambda *a: 'She had hit the rate limit of her patience.'
        self.assertEqual(client.generate_content('p'), 'She had hit the rate limit of her patience.')
        for error in (ValueError('bad JSON'), api.IncompleteGenerationError('cut')):
            client._generate_content_once = Mock(side_effect=error)
            with self.assertRaises(type(error)):
                client.generate_content('p')

        # a stated reset is waited for directly, in its own zone, rolling over to tomorrow
        notice = "You've hit your session limit · resets 4pm (America/Argentina/Buenos_Aires)"
        at = api.datetime(2026, 9, 24, 16, 33, tzinfo=timezone.utc)  # 13:33 in Buenos Aires
        self.assertEqual(api.limit_reset_wait(notice, at), 2 * 3600 + 27 * 60 + 60)
        self.assertEqual(api.limit_reset_wait('resets 9:30am', api.datetime(2026, 9, 24, 10, 0)), 23.5 * 3600 + 60)
        self.assertIsNone(api.limit_reset_wait("You've hit your session limit"))


if __name__ == '__main__':
    unittest.main()
