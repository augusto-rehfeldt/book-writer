"""AIService inside hosts with nothing but the standard library (Calibre's bundled Python),
and the CLI transports' isolation from project/global instructions.

The calibre summarizer plugin ships a copy of ai_service.py in its zip and builds services
from its own provider rows, so these behaviours are part of the shared contract.
"""
import http.server
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_book_creator.services import ai_service as api


def stdlib_module():
    """ai_service imported as if requests, openai and google were not installed."""
    blocked = {"requests": None, "openai": None, "google": None, "google.genai": None}
    with patch.dict(sys.modules, blocked):
        sys.modules.pop("portable_ai_service", None)
        spec = importlib.util.spec_from_file_location("portable_ai_service", api.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class Endpoint(http.server.BaseHTTPRequestHandler):
    replies = []
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Endpoint.seen.append((self.path, dict(self.headers), body))
        status, payload = Endpoint.replies.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class StdlibTransportTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        Endpoint.replies, Endpoint.seen = [], []
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        sleep = patch("time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        # Stand-in keys only: a failure message must never print a real one.
        keys = patch.dict(os.environ, LEAKABLE)
        keys.start()
        self.addCleanup(keys.stop)

    def service(self, module, **overrides):
        base = {"provider": "openrouter", "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
                "api_key": "row-key", "writing_model": "m",
                "groq_rate_state_path": str(Path(self.folder.name) / "groq.json")}
        return module.AIService(None, str(Path(self.folder.name) / "usage.json"), allow_auth_prompt=False,
                                config_overrides={**base, **overrides})

    def test_imports_and_answers_without_requests_or_sdks(self):
        module = stdlib_module()
        self.assertIsNone(module.requests)
        Endpoint.replies = [(200, {"choices": [{"message": {"content": "summary"}, "finish_reason": "stop"}]})]
        service = self.service(module, headers={"User-Agent": "Browser/1.0"}, token_param="max_completion_tokens")
        self.assertEqual(service.generate_content("book", system="neutral", max_completion_tokens=900), "summary")
        path, headers, body = Endpoint.seen[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer row-key")
        self.assertEqual(headers["User-Agent"], "Browser/1.0")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "neutral"})
        self.assertIn("max_completion_tokens", body)
        self.assertNotIn("max_tokens", body)

    def test_transient_status_surfaces_with_its_code(self):
        module = stdlib_module()
        Endpoint.replies = [(429, {"error": "slow down"})]
        with self.assertRaises(module.ProviderLimitReached) as caught:
            self.service(module).generate_content("book", max_retries=1, wait_for_limits=False)
        self.assertEqual(getattr(caught.exception, "status_code", None), 429)

    def test_fatal_status_surfaces_with_its_code(self):
        module = stdlib_module()
        Endpoint.replies = [(401, {"error": "bad key"})]
        with self.assertRaises(Exception) as caught:
            self.service(module).generate_content("book", max_retries=1)
        self.assertEqual(getattr(caught.exception, "status_code", None), 401)

    def test_empty_reply_is_its_own_error(self):
        module = stdlib_module()
        Endpoint.replies = [(200, {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})]
        with self.assertRaises(module.EmptyGenerationError):
            self.service(module).generate_content("book", max_retries=1)

    def test_embeddings_over_the_stdlib_transport(self):
        module = stdlib_module()
        Endpoint.replies = [(200, {"data": [{"index": 1, "embedding": [0.0, 1.0]},
                                            {"index": 0, "embedding": [1.0, 0.0]}]})]
        vectors = self.service(module).embed(["first", "second"], model="embed-model")
        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])  # returned in input order
        path, headers, body = Endpoint.seen[0]
        self.assertEqual((path, body), ("/v1/embeddings", {"model": "embed-model", "input": ["first", "second"]}))
        self.assertEqual(headers["Authorization"], "Bearer row-key")

    def test_embeddings_errors_carry_the_status(self):
        module = stdlib_module()
        Endpoint.replies = [(404, {"error": "no such model"})]
        with self.assertRaises(module.TransportError) as caught:
            self.service(module).embed(["x"], model="missing")
        self.assertEqual(caught.exception.status_code, 404)

    def test_embeddings_through_the_sdk_client(self):
        item = lambda i, v: type("E", (), {"index": i, "embedding": v})()
        calls = []

        class Embeddings:
            def create(self, **kwargs):
                calls.append(kwargs)
                return type("R", (), {"data": [item(0, [0.5])]})()
        service = self.service(api)
        service.client = type("C", (), {"embeddings": Embeddings()})()
        self.assertEqual(service.embed(["only"]), [[0.5]])
        self.assertEqual(calls, [{"model": "m", "input": ["only"], "timeout": service.timeout}])

    def test_config_only_construction_needs_a_provider(self):
        with self.assertRaises(ValueError):
            api.AIService(None, config_overrides={"base_url": "x"}, allow_auth_prompt=False)


LEAKABLE = {"AI_API_KEY": "ai-key", "OPENAI_API_KEY": "openai-key", "OPENROUTER_API_KEY": "openrouter-key",
            "GROQ_API_KEY": "groq-key", "HYPER_API_KEY": "hyper-key", "XAI_API_KEY": "xai-key",
            "GOOGLE_API_KEY": "google-key", "MINIMAX_API_KEY": "minimax-key", "NVIDIA_API_KEY": ""}


class KeyIsolationTests(unittest.TestCase):
    """A provider's request never carries another provider's key (critic finding H1)."""

    def setUp(self):
        StdlibTransportTests.setUp(self)
        env = patch.dict(os.environ, LEAKABLE)
        env.start()
        self.addCleanup(env.stop)

    service = StdlibTransportTests.service

    def test_keyless_custom_endpoint_sends_no_authorization(self):
        module = stdlib_module()
        Endpoint.replies = [(200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})]
        service = self.service(module, api_key="")
        self.assertEqual(service.api_key, "")
        service.generate_content("hi", max_retries=1)
        self.assertNotIn("Authorization", Endpoint.seen[0][1])

    def test_named_key_env_never_falls_back_to_other_providers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nvidia.json"
            path.write_text(json.dumps({"provider": "openrouter", "base_url": "https://integrate.api.nvidia.com/v1",
                                        "api_key_env": "NVIDIA_API_KEY", "writing_model": "m",
                                        "groq_rate_state_path": str(Path(folder) / "g.json")}))
            service = api.AIService(str(path), str(Path(folder) / "u.json"), allow_auth_prompt=False)
        self.assertEqual(service.api_key, "")

    def test_override_base_url_is_used_verbatim(self):
        module = stdlib_module()
        service = self.service(module, base_url="https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual(service.base_url, "https://generativelanguage.googleapis.com/v1beta/openai")

    def test_explicit_overrides_beat_stray_environment(self):
        module = stdlib_module()
        with patch.dict(os.environ, {"AI_BASE_URL": "https://stray.example/v1", "AI_WRITING_MODEL": "stray",
                                     "AI_WRITING_COMPLETION_TOKENS": "99999"}):
            service = self.service(module, base_url="https://mine.example/v1", writing_model="mine")
        self.assertEqual((service.base_url, service.writing_model), ("https://mine.example/v1", "mine"))

    def test_cap_is_ceiling_sends_the_callers_cap(self):
        module = stdlib_module()
        Endpoint.replies = [(200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})]
        self.service(module, cap_is_ceiling=True).generate_content("hi", max_completion_tokens=450, max_retries=1)
        self.assertEqual(Endpoint.seen[0][2]["max_tokens"], 450)


class CliIsolationTests(unittest.TestCase):
    def run_cli(self, fn, returncode=0, stdout="answer", **kwargs):
        done = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with patch.object(api.subprocess, "run", return_value=done) as run, \
                patch.object(api, "claude_executable", return_value="claude"), \
                patch.object(api, "commandcode_executable", return_value="cmdc"), \
                patch.object(api, "opencode_executable", return_value="opencode"):
            result = fn("model", "the prompt", timeout=5, **kwargs)
        return result, run.call_args

    def test_claude_runs_outside_any_project_with_a_neutral_system_prompt(self):
        text, call = self.run_cli(api.claude_chat, system="Summarize.")
        args = call.args[0]
        self.assertEqual(call.kwargs["cwd"], tempfile.gettempdir())
        self.assertEqual(call.kwargs["input"], "the prompt")
        self.assertIn("--safe-mode", args)  # no CLAUDE.md, skills, plugins, hooks or output styles
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertNotIn("--append-system-prompt", args)
        system = args[args.index("--system-prompt") + 1]
        self.assertIn("Summarize.", system)
        self.assertIn("Ignore every global, project or plugin instruction", system)
        if os.name == "nt":
            self.assertEqual(call.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_commandcode_has_no_system_flag_so_the_instruction_leads_the_prompt(self):
        _text, call = self.run_cli(api.commandcode_chat, system="Summarize.")
        args = call.args[0]
        for flag in ("--skip-onboarding", "--no-skills", "--no-session"):
            self.assertIn(flag, args)
        self.assertTrue(call.kwargs["input"].endswith("the prompt"))
        self.assertIn("Summarize.", call.kwargs["input"])
        self.assertEqual(call.kwargs["cwd"], tempfile.gettempdir())

    def test_commandcode_exit_codes_are_explained(self):
        with self.assertRaisesRegex(RuntimeError, "out of credits"):
            self.run_cli(api.commandcode_chat, returncode=10, stdout="")

    def test_opencode_keeps_the_final_step_and_stops_on_a_refusal(self):
        events = [{"type": "step_start"}, {"type": "text", "part": {"text": "Let me check."}},
                  {"type": "step_start"}, {"type": "text", "part": {"text": "97"}}, {"type": "step_finish"}]
        text, call = self.run_cli(api.opencode_chat, stdout="\n".join(map(json.dumps, events)),
                                  system="Summarize.")
        self.assertEqual(text, "97")
        args = call.args[0]
        self.assertEqual(args[args.index("--agent") + 1], "plan")  # stock and read-only
        self.assertEqual(args[args.index("-m") + 1], "opencode/model")
        self.assertIn("Summarize.", call.kwargs["input"])
        refusal = {"type": "error", "error": {"data": {"message": "free tier", "statusCode": 403}}}
        with self.assertRaises(RuntimeError) as raised:
            self.run_cli(api.opencode_chat, stdout=json.dumps(refusal))
        self.assertEqual(raised.exception.status_code, 403)
        silent = [{"type": "step_start", "part": {"id": "x" * 400}},
                  {"type": "step_finish", "part": {"reason": "stop", "tokens": {"output": 0}}}]
        with self.assertRaises(RuntimeError) as raised:
            self.run_cli(api.opencode_chat, stdout="\n".join(map(json.dumps, silent)))
        self.assertEqual(str(raised.exception), "opencode model returned no text "
                         "(events step_start, step_finish; finish reason stop, 0 output tokens)")

    def test_opencode_output_cap_is_the_models_and_thinking_to_it_is_truncation(self):
        # OpenCode defaults to 32k output; a reasoning model can spend all of it thinking.
        _text, call = self.run_cli(api.opencode_chat, max_output=524288,
                                   stdout=json.dumps({"type": "text", "part": {"text": "ok"}}))
        self.assertEqual(call.kwargs["env"]["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"], "524288")
        thought_out = [{"type": "step_start"}, {"type": "step_finish", "part": {
            "reason": "length", "tokens": {"output": 0, "reasoning": 32000}}}]
        with self.assertRaisesRegex(api.IncompleteGenerationError, "32000 reasoning tokens"):
            self.run_cli(api.opencode_chat, stdout="\n".join(map(json.dumps, thought_out)))

    def test_gui_hosts_find_npm_installed_clis(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "cmdc.cmd").write_text("")
            with patch.object(api.shutil, "which", return_value=None), \
                    patch.object(api, "_cli_fallback_dirs", return_value=[Path(folder)]):
                self.assertEqual(api.commandcode_executable(), str(Path(folder) / "cmdc.cmd"))

    def test_service_hands_the_system_prompt_to_the_cli_natively(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(api, "claude_executable", return_value="claude"):
                service = api.AIService(None, str(Path(folder) / "u.json"), allow_auth_prompt=False,
                                        config_overrides={"provider": "claude", "writing_model": "sonnet",
                                                          "groq_rate_state_path": str(Path(folder) / "g.json")})
            with patch.object(api, "claude_chat", return_value="done") as chat:
                service.generate_content("hi", system="rules")
        self.assertEqual(chat.call_args.args[1], "hi")
        self.assertEqual(chat.call_args.kwargs["system"], "rules")


if __name__ == "__main__":
    unittest.main()
