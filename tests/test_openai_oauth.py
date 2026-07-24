import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_book_creator import cli
from ai_book_creator.services import ai_service
from ai_book_creator.services.ai_service import AIService


class OpenAIOAuthTests(unittest.TestCase):
    def test_uses_local_proxy_without_reusing_provider_keys(self):
        config = Path(__file__).parents[1] / "ai_book_creator" / "config" / "ai_config_openai_oauth.json"
        with patch.object(ai_service, "ensure_openai_oauth_proxy") as ensure, patch.dict(
            os.environ, {"OPENAI_API_KEY": "paid-key"}
        ):
            service = AIService(str(config))
        ensure.assert_called_once_with()
        self.assertEqual(service.provider, "openai-oauth")
        self.assertEqual(service.api_key, "openai-oauth")
        self.assertEqual(service.base_url, "http://127.0.0.1:10531/v1")

    def test_starts_missing_proxy_with_npx(self):
        with patch.object(ai_service, "_openai_oauth_proxy_running", side_effect=[False, True]), patch.object(
            ai_service.shutil, "which", return_value="npx"
        ), patch.object(ai_service.subprocess, "run") as run:
            ai_service.ensure_openai_oauth_proxy()
        run.assert_called_once_with(["npx", "openai-oauth@latest", "--detach"], check=True)

    def test_loads_live_text_models_without_inventing_context(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "data": [
                {"id": "gpt-5.6-sol", "context_window": 272000, "max_output_tokens": 128000},
                {"id": "gpt-5.4-mini"},
                {"id": "gpt-image-2"},
            ]
        }).encode()
        with patch.object(cli, "ensure_openai_oauth_proxy"), patch.object(cli, "urlopen", return_value=response):
            models = cli._load_openai_oauth_models()
        self.assertEqual(models["gpt-5.6-sol"], (272000, 128000))
        self.assertEqual(models["gpt-5.4-mini"], (None, None))
        self.assertNotIn("gpt-image-2", models)

    def test_terra_is_initial_oauth_default(self):
        options = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.4-mini")
        with patch.object(cli, "_load_provider_state", return_value={"openai_model": "gpt-5.4-mini"}):
            default = cli._load_last_openai_model("gpt-5.6-terra", options, "openai_oauth_model")
        self.assertEqual(default, "gpt-5.6-terra")


if __name__ == "__main__":
    unittest.main()
