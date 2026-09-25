"""Contract pieces other workspace projects build on when they use this AIService.

Consumers (bandido, impostor, book-watch, lamplight, calibre summarizer, story atlas,
mathforge, music writer) pick a provider by name, may bring their own key and
endpoint, and must never be handed another provider's key.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_book_creator import cli
from ai_book_creator.services import ai_service as api


def write_config(folder, **values):
    path = Path(folder) / "config.json"
    path.write_text(json.dumps({"provider": "hyper", "writing_model": "m", "api_key_env": "HYPER_API_KEY",
                                "groq_rate_state_path": str(Path(folder) / "groq.json"), **values}))
    return str(path)


def build(folder, **kwargs):
    return api.AIService(write_config(folder), str(Path(folder) / "usage.json"),
                         allow_auth_prompt=False, client_max_retries=0, **kwargs)


class ConfigOverrideTests(unittest.TestCase):
    def test_overrides_merge_over_the_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "env-key"}):
            service = build(folder, config_overrides={"base_url": "https://gateway.example/v1", "timeout": 30})
        self.assertEqual((service.base_url, service.timeout), ("https://gateway.example/v1", 30))
        # Was "env-key": a consumer's own endpoint must not receive the provider's key
        # unless it asks for it (critic finding H1) -- by api_key or api_key_env override.
        self.assertEqual(service.api_key, "")
        self.assertEqual(service.writing_model, "m")
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "env-key"}):
            service = build(folder, config_overrides={"base_url": "https://gateway.example/v1",
                                                      "api_key_env": "HYPER_API_KEY"})
        self.assertEqual(service.api_key, "env-key")

    def test_explicit_key_beats_every_environment_fallback(self):
        env = {"HYPER_API_KEY": "", "AI_API_KEY": "other", "OPENAI_API_KEY": "openai-key"}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env):
            service = build(folder, config_overrides={"api_key": "consumer-key"})
        self.assertEqual(service.api_key, "consumer-key")

    def test_no_overrides_keeps_existing_behaviour(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "env-key"}):
            service = build(folder)
        self.assertEqual((service.api_key, service.base_url), ("env-key", "https://hyper.charm.land/v1"))


class FailFastTests(unittest.TestCase):
    """Interactive consumers (a served report, a game turn) cannot wait hours for a reset."""

    def client(self, reply):
        client = object.__new__(api.AIService)
        client.provider_label, client.writing_model, client.review_model = "hyper", "m", "m"
        calls = []

        def once(*args):
            # Bounded: a regression that waits again must fail here, not hang the suite.
            calls.append(args)
            if len(calls) > 3:
                raise AssertionError("generate_content kept waiting instead of raising")
            return reply() if callable(reply) else reply
        client._generate_content_once = once
        return client

    def test_limit_notice_raises_instead_of_waiting(self):
        client = self.client("You've hit your usage limit. Try again at 5pm.")
        with patch.object(api.time, "sleep") as sleep:
            with self.assertRaises(api.ProviderLimitReached) as caught:
                client.generate_content("p", wait_for_limits=False)
        sleep.assert_not_called()
        self.assertIn("usage limit", str(caught.exception))

    def test_limit_error_raises_instead_of_waiting(self):
        def boom():
            raise RuntimeError("429 rate limit exceeded")
        with patch.object(api.time, "sleep") as sleep:
            with self.assertRaises(api.ProviderLimitReached):
                self.client(boom).generate_content("p", wait_for_limits=False)
        sleep.assert_not_called()

    def test_normal_text_is_returned(self):
        self.assertEqual(self.client("fine").generate_content("p", wait_for_limits=False), "fine")


class Chunk:
    def __init__(self, text="", finish=None, reasoning=None):
        delta = type("D", (), {"content": text, "reasoning_content": reasoning})()
        self.choices = [type("C", (), {"delta": delta, "finish_reason": finish})()]


class ChatOptionTests(unittest.TestCase):
    """lamplight's router needs system prompts, temperature and streamed replies."""

    def setUp(self):
        sleep = patch.object(api.time, "sleep")  # the service's retry delays must not run for real
        sleep.start()
        self.addCleanup(sleep.stop)

    def service(self, folder, replies, **config):
        service = build(folder, config_overrides=config)
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return replies.pop(0)
        service.client = type("Client", (), {})()
        service.client.chat = type("Chat", (), {})()
        service.client.chat.completions = type("Completions", (), {"create": staticmethod(create)})()
        return service, calls

    def test_system_and_temperature_reach_chat_endpoints(self):
        reply = type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})(),
                                                           "finish_reason": "stop"})()]})()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "k"}):
            service, calls = self.service(folder, [reply])
            self.assertEqual(service.generate_content("hi", system="be brief", temperature=0.0), "ok")
        self.assertEqual(calls[0]["messages"], [{"role": "system", "content": "be brief"},
                                                {"role": "user", "content": "hi"}])
        self.assertEqual(calls[0]["temperature"], 0.0)

    def test_last_usage_is_what_the_provider_reported(self):
        usage = type("U", (), {"prompt_tokens": 12, "completion_tokens": 5})()
        reply = type("R", (), {"usage": usage, "choices": [type("C", (), {
            "message": type("M", (), {"content": "ok"})(), "finish_reason": "stop"})()]})()
        bare = type("R", (), {"choices": reply.choices})()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "k"}):
            service, _calls = self.service(folder, [reply, bare])
            service.generate_content("hi")
            self.assertEqual(service.last_usage, {"prompt_tokens": 12, "completion_tokens": 5})
            service.generate_content("hi")
            self.assertIsNone(service.last_usage)  # never an estimate dressed up as billing

    def test_no_delay_after_the_last_attempt(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "k"}):
            service, _calls = self.service(folder, [])  # empty: every call raises IndexError
            with self.assertRaises(IndexError):
                service.generate_content("hi", max_retries=1)
        api.time.sleep.assert_not_called()

    def test_defaults_send_neither(self):
        reply = type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})(),
                                                           "finish_reason": "stop"})()]})()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "k"}):
            service, calls = self.service(folder, [reply])
            service.generate_content("hi")
        self.assertEqual(calls[0]["messages"], [{"role": "user", "content": "hi"}])
        self.assertNotIn("temperature", calls[0])
        self.assertNotIn("stream", calls[0])

    def test_stream_config_streams_and_joins_text_only(self):
        stream = [Chunk("Hel", reasoning="thinking"), Chunk("lo"), Chunk("", finish="stop")]
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "k"}):
            service, calls = self.service(folder, [stream], stream=True)
            self.assertEqual(service.generate_content("hi"), "Hello")
        self.assertTrue(calls[0]["stream"])

    def test_truncated_stream_retries_with_a_larger_cap(self):
        cut = [Chunk("half", finish="length")]
        whole = [Chunk("whole", finish="stop")]
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HYPER_API_KEY": "k"}), \
                patch.object(api.time, "sleep"):
            service, calls = self.service(folder, [cut, whole], stream=True)
            self.assertEqual(service.generate_content("hi", max_retries=2), "whole")
        self.assertGreater(calls[1]["max_tokens"], calls[0]["max_tokens"])

    def test_cli_providers_get_the_system_prompt_natively(self):
        # Was "prepended"; the CLI transports now take it as a real system prompt
        # (claude's --append-system-prompt), see test_shared_portable.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"provider": "claude", "writing_model": "sonnet",
                                        "groq_rate_state_path": str(Path(folder) / "groq.json")}))
            with patch.object(api, "claude_executable", return_value="claude"):
                service = api.AIService(str(path), str(Path(folder) / "usage.json"), allow_auth_prompt=False)
            with patch.object(api, "claude_chat", return_value="done") as chat:
                service.generate_content("hi", system="rules")
        self.assertEqual((chat.call_args.args[1], chat.call_args.kwargs["system"]), ("hi", "rules"))


class NvidiaProviderTests(unittest.TestCase):
    def test_nvidia_nim_is_a_catalogue_provider(self):
        """Bandido's NVIDIA NIM players moved here; the OpenAI-compatible branch serves it."""
        self.assertIn("nvidia", cli.CATALOGUE_PROVIDERS)
        data = json.loads(Path(cli.PROVIDER_CONFIG_MAP["nvidia"]).read_text(encoding="utf-8"))
        self.assertEqual(data["base_url"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(data["api_key_env"], "NVIDIA_API_KEY")
        self.assertIn(data["writing_model"], data["models"])
        with patch.object(cli, "_live_model_ids", return_value=None):
            self.assertIn("deepseek-ai/deepseek-v4-pro", cli._provider_models("nvidia"))
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nv-key"}):
            service = api.AIService(cli.provider_config_path("nvidia"), allow_auth_prompt=False)
        self.assertEqual((service.api_key, service.base_url, service.provider_label),
                         ("nv-key", "https://integrate.api.nvidia.com/v1", "integrate.api.nvidia.com"))



class ProviderConfigPathTests(unittest.TestCase):
    def test_prefers_the_local_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "ai_config_x.json"
            base.write_text("{}")
            with patch.dict(cli.PROVIDER_CONFIG_MAP, {"x": str(base)}):
                self.assertEqual(cli.provider_config_path("x"), str(base))
                local = base.with_name("ai_config_x.local.json")
                local.write_text("{}")
                self.assertEqual(cli.provider_config_path("x"), str(local))

    def test_codex_alias_and_unknown_provider(self):
        self.assertEqual(cli.provider_config_path("codex"), cli.provider_config_path("openai-oauth"))
        with self.assertRaises(KeyError):
            cli.provider_config_path("nope")


if __name__ == "__main__":
    unittest.main()
