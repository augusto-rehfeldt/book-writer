from __future__ import annotations

import json
import os
import math
import re
import time
from datetime import datetime
from typing import Optional, Dict, Any, Iterable, List, Tuple

import requests

# Try optional OpenAI python client
try:
    from openai import OpenAI  # type: ignore
    _HAS_OPENAI_CLIENT = True
except Exception:
    OpenAI = None  # type: ignore
    _HAS_OPENAI_CLIENT = False

# Try optional Google Gemini client
try:
    from google import genai  # type: ignore
    _HAS_GEMINI_CLIENT = True
except Exception:
    genai = None  # type: ignore
    _HAS_GEMINI_CLIENT = False


DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "ai_config_google.json"
)
DEFAULT_USAGE_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "ai_usage_state.json"
)
DEFAULT_GROQ_RATE_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "groq_usage_state.json"
)


class UsageLimitExceeded(RuntimeError):
    def __init__(
        self,
        provider: str,
        metric: str,
        tokens_used: int,
        token_limit: int,
        model_name: str,
        usage_state_path: str,
    ):
        self.provider = provider
        self.metric = metric
        self.tokens_used = tokens_used
        self.token_limit = token_limit
        self.model_name = model_name
        self.usage_state_path = usage_state_path
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        return (
            f"{self.provider.title()} {self.metric} limit exceeded for model '{self.model_name}': "
            f"{self.tokens_used:,}/{self.token_limit:,} used. "
            f"Progress has been cached at {self.usage_state_path}."
        )


class DailyTokenBudgetExceeded(UsageLimitExceeded):
    def __init__(
        self,
        bucket: str,
        tokens_used: int,
        token_limit: int,
        model_name: str,
        usage_state_path: str,
    ):
        super().__init__(
            provider="OpenAI",
            metric=f"{bucket} daily token budget",
            tokens_used=tokens_used,
            token_limit=token_limit,
            model_name=model_name,
            usage_state_path=usage_state_path,
        )


class AIService:
    def __init__(
        self,
        config_path: Optional[str] = None,
        usage_state_path: Optional[str] = None,
    ):
        """
        Initialize AIService.

        Config (ai_config_google.json) example:
        {
            "provider": "openai" or "google" or "http",
            "use_openai_client": true,
            "model": "gpt-5...",
            "base_url": "https://api.openai.com",
            "timeout": 60
        }
        """
        self.config = self._load_config(config_path or os.getenv("AI_CONFIG_PATH", DEFAULT_CONFIG_PATH))
        if not self.config:
            raise ValueError("AIService configuration could not be loaded")

        self.provider = self.config.get("provider", "openai").lower()
        self.api_key = self._resolve_api_key()
        self.writing_model = os.getenv("AI_WRITING_MODEL", self.config.get("writing_model", "gpt-5-mini"))
        self.review_model = os.getenv("AI_REVIEW_MODEL", self.config.get("review_model", "gpt-5"))
        self.base_url = self._resolve_base_url()
        self.use_openai_client = bool(self.config.get("use_openai_client", True))
        self.timeout = int(self.config.get("timeout", 900))
        self.openai_daily_token_limits = self.config.get(
            "openai_daily_token_limits",
            {"pro": 250000, "mini": 2500000},
        )
        self.groq_rate_limits = self.config.get(
            "groq_rate_limits",
            {"tpm": 70000, "rpm": 30, "rpd": 250},
        )
        self.groq_daily_token_limit = int(self.config.get("groq_daily_token_limit", 500000))
        self.usage_state_path = usage_state_path or os.getenv(
            "AI_USAGE_STATE_PATH",
            self.config.get("usage_state_path", DEFAULT_USAGE_STATE_PATH),
        )
        self.groq_rate_state_path = os.getenv(
            "AI_GROQ_RATE_STATE_PATH",
            self.config.get("groq_rate_state_path", DEFAULT_GROQ_RATE_STATE_PATH),
        )
        self._budget_pause_reason = ""
        self._budget_pause_requested = False
        self._usage_state = self._load_usage_state()
        self._groq_rate_state = self._load_groq_rate_state()

        if not self.api_key and self.provider != "http":
            print(
                "Warning: No API key provided. Set the provider-specific env var "
                "(OPENAI_API_KEY, GROQ_API_KEY, GOOGLE_API_KEY, or AI_API_KEY) "
                "or update your local override file."
            )

        self._init_client()
        print("AI Service initialized with provider:", self.provider)

    def _load_config(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception as e:
            print(f"Failed to load config from '{path}': {e}")
            return None

    def _resolve_api_key(self) -> str:
        env_key_map = {
            "openai": "OPENAI_API_KEY",
            "groq": "GROQ_API_KEY",
            "google": "GOOGLE_API_KEY",
        }
        provider_env = str(self.config.get("api_key_env") or env_key_map.get(self.provider, "AI_API_KEY"))
        candidates = [
            os.getenv(provider_env, ""),
            os.getenv("AI_API_KEY", ""),
            os.getenv("OPENAI_API_KEY", ""),
            os.getenv("GROQ_API_KEY", ""),
            os.getenv("GOOGLE_API_KEY", ""),
            self.config.get("api_key", ""),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
        return ""

    def _resolve_base_url(self) -> str:
        default_base_urls = {
            "openai": "https://api.openai.com/v1",
            "groq": "https://api.groq.com/openai/v1",
        }
        raw_base_url = os.getenv("AI_BASE_URL", self.config.get("base_url", default_base_urls.get(self.provider, "https://api.openai.com/v1")))
        base_url = raw_base_url.rstrip("/")

        if self.provider in ("openai", "groq", "http") and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        return base_url

    def _load_usage_state(self) -> Dict[str, Any]:
        state = self._default_usage_state()
        if not self.usage_state_path:
            return state

        if os.path.exists(self.usage_state_path):
            try:
                with open(self.usage_state_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    state.update({k: v for k, v in loaded.items() if k != "buckets"})
                    if isinstance(loaded.get("buckets"), dict):
                        state["buckets"].update(loaded["buckets"])
            except Exception as e:
                print(f"Warning: Could not load usage state '{self.usage_state_path}': {e}. Starting fresh.")

        self._reset_usage_state_if_needed(state)
        return state

    def _default_usage_state(self) -> Dict[str, Any]:
        return {
            "date": datetime.now().date().isoformat(),
            "paused": False,
            "pause_reason": "",
            "buckets": {
                "pro": {
                    "tokens": 0,
                    "limit": int(self.openai_daily_token_limits.get("pro", 250000)),
                    "models": {},
                },
                "mini": {
                    "tokens": 0,
                    "limit": int(self.openai_daily_token_limits.get("mini", 2500000)),
                    "models": {},
                },
            },
        }

    def _default_groq_rate_state(self) -> Dict[str, Any]:
        now = datetime.now()
        return {
            "date": now.date().isoformat(),
            "minute_window_start": now.replace(second=0, microsecond=0).isoformat(),
            "minute_tokens": 0,
            "minute_requests": 0,
            "day_tokens": 0,
            "day_requests": 0,
            "paused": False,
            "pause_reason": "",
            "models": {},
        }

    def _load_groq_rate_state(self) -> Dict[str, Any]:
        state = self._default_groq_rate_state()
        if not self.groq_rate_state_path:
            return state

        if os.path.exists(self.groq_rate_state_path):
            try:
                with open(self.groq_rate_state_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    state.update({k: v for k, v in loaded.items() if k != "models"})
                    if isinstance(loaded.get("models"), dict):
                        state["models"].update(loaded["models"])
            except Exception as e:
                print(f"Warning: Could not load Groq rate state '{self.groq_rate_state_path}': {e}. Starting fresh.")

        self._reset_groq_rate_state_if_needed(state)
        return state

    def _reset_usage_state_if_needed(self, state: Dict[str, Any]) -> None:
        today = datetime.now().date().isoformat()
        if state.get("date") != today:
            state.clear()
            state.update(self._default_usage_state())
            self._budget_pause_requested = False
            self._budget_pause_reason = ""
            self._save_usage_state(state)

    def _reset_groq_rate_state_if_needed(self, state: Dict[str, Any]) -> None:
        now = datetime.now()
        today = now.date().isoformat()
        current_minute = now.replace(second=0, microsecond=0).isoformat()

        if state.get("date") != today:
            state.clear()
            state.update(self._default_groq_rate_state())
            self._budget_pause_requested = False
            self._budget_pause_reason = ""
            self._save_groq_rate_state(state)
            return

        if state.get("minute_window_start") != current_minute:
            state["minute_window_start"] = current_minute
            state["minute_tokens"] = 0
            state["minute_requests"] = 0
            self._save_groq_rate_state(state)

    def _save_usage_state(self, state: Optional[Dict[str, Any]] = None) -> None:
        if not self.usage_state_path:
            return

        payload = state or self._usage_state
        try:
            os.makedirs(os.path.dirname(self.usage_state_path), exist_ok=True)
            with open(self.usage_state_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: Could not save usage state '{self.usage_state_path}': {e}")

    def _save_groq_rate_state(self, state: Optional[Dict[str, Any]] = None) -> None:
        if not self.groq_rate_state_path:
            return

        payload = state or self._groq_rate_state
        try:
            os.makedirs(os.path.dirname(self.groq_rate_state_path), exist_ok=True)
            with open(self.groq_rate_state_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: Could not save Groq rate state '{self.groq_rate_state_path}': {e}")

    def _bucket_for_model(self, model_name: str) -> str:
        return "mini" if "mini" in model_name.lower() else "pro"

    def _estimate_tokens(self, text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    def _groq_safety_margin(self) -> float:
        return float(self.config.get("groq_safety_margin", 0.1))

    def _apply_safety_margin(self, token_count: int, margin: Optional[float] = None) -> int:
        margin = self._groq_safety_margin() if margin is None else margin
        margin = max(0.0, float(margin))
        if margin <= 0:
            return int(token_count)
        return max(1, int(math.ceil(token_count * (1.0 + margin))))

    def get_prompt_token_budget(self) -> int:
        budgets = self.config.get(
            "prompt_token_budgets",
            {"openai": 12000, "groq": 8000, "google": 12000, "http": 12000},
        )
        budget = int(budgets.get(self.provider, budgets.get("http", 12000)))
        if self.provider == "groq":
            budget = max(1000, int(budget * (1.0 - self._groq_safety_margin())))
        return budget

    def _clip_text_by_tokens(self, text: str, max_tokens: int) -> str:
        max_chars = max(1, max_tokens * 4)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def build_sectioned_prompt(
        self,
        instruction: str,
        sections: Iterable[Tuple[str, str]],
        max_prompt_tokens: Optional[int] = None,
        section_token_caps: Optional[Dict[str, int]] = None,
        safety_margin: Optional[float] = None,
    ) -> str:
        section_token_caps = section_token_caps or {}
        max_prompt_tokens = max_prompt_tokens or self.get_prompt_token_budget()
        if safety_margin is None:
            safety_margin = self._groq_safety_margin() if self.provider == "groq" else 0.0
        else:
            safety_margin = max(0.0, float(safety_margin))
        effective_max_prompt_tokens = max(1000, int(max_prompt_tokens * (1.0 - safety_margin)))

        instruction = instruction.strip()
        prepared_sections: List[Tuple[str, str]] = []
        for heading, text in sections:
            cap = int(section_token_caps.get(heading, max(250, effective_max_prompt_tokens // 3)))
            if safety_margin > 0:
                cap = max(100, int(cap * (1.0 - safety_margin)))
            prepared_sections.append((heading, self._clip_text_by_tokens(text.strip(), cap)))

        def render(parts: List[Tuple[str, str]]) -> str:
            rendered = [instruction]
            for heading, text in parts:
                rendered.append(f"{heading}: {text}".strip())
            return "\n\n".join(rendered).strip()

        prompt = render(prepared_sections)
        estimate = self._estimate_tokens(prompt)
        if estimate <= effective_max_prompt_tokens:
            return prompt

        # Shrink the largest sections first until the prompt fits comfortably.
        mutable_sections = list(prepared_sections)
        while estimate > effective_max_prompt_tokens and mutable_sections:
            mutable_sections.sort(key=lambda item: len(item[1]), reverse=True)
            heading, text = mutable_sections[0]
            current_tokens = self._estimate_tokens(text)
            if current_tokens <= 250:
                break
            new_token_cap = max(250, int(current_tokens * 0.75))
            mutable_sections[0] = (heading, self._clip_text_by_tokens(text, new_token_cap))
            prompt = render(mutable_sections)
            estimate = self._estimate_tokens(prompt)

        if estimate > effective_max_prompt_tokens:
            instruction_cap = max(500, effective_max_prompt_tokens - 500)
            prompt = self._clip_text_by_tokens(prompt, instruction_cap)

        return prompt

    def _shrink_prompt_text(self, prompt: str, shrink_factor: float = 0.8) -> str:
        target_tokens = max(1000, int(self.get_prompt_token_budget() * shrink_factor))
        return self._clip_text_by_tokens(prompt, target_tokens)

    def _groq_limit_value(self, key: str) -> int:
        return int(self.groq_rate_limits.get(key, {"tpm": 70000, "rpm": 30, "rpd": 250}.get(key, 0)))

    def _groq_seconds_until_next_minute(self) -> int:
        now = datetime.now()
        return max(1, 60 - now.second)

    def _groq_wait_for_next_minute(self, reason: str) -> None:
        wait_seconds = self._groq_seconds_until_next_minute()
        print(f"[groq] {reason} Waiting {wait_seconds} seconds for the rate window to reset...")
        time.sleep(wait_seconds)
        self._reset_groq_rate_state_if_needed(self._groq_rate_state)

    def _groq_preflight_limit_check(self, model_name: str, prompt: str) -> None:
        self._reset_groq_rate_state_if_needed(self._groq_rate_state)

        prompt_tokens = self._apply_safety_margin(self._estimate_tokens(prompt))
        reserve_tokens = int(self.config.get("groq_tpm_output_reserve", 2048))
        tokens_to_spend = prompt_tokens + self._apply_safety_margin(max(0, reserve_tokens))

        minute_tokens = int(self._groq_rate_state.get("minute_tokens", 0))
        minute_requests = int(self._groq_rate_state.get("minute_requests", 0))
        day_tokens = int(self._groq_rate_state.get("day_tokens", 0))
        day_requests = int(self._groq_rate_state.get("day_requests", 0))

        tpm_limit = self._groq_limit_value("tpm")
        rpm_limit = self._groq_limit_value("rpm")
        rpd_limit = self._groq_limit_value("rpd")
        tpd_limit = int(self.groq_daily_token_limit)

        if minute_requests >= rpm_limit:
            self._groq_wait_for_next_minute(
                f"Groq RPM limit reached for '{model_name}' ({minute_requests}/{rpm_limit} requests this minute)."
            )
            return

        if day_requests >= rpd_limit:
            self._budget_pause_requested = True
            self._budget_pause_reason = (
                f"Groq RPD limit reached for '{model_name}' ({day_requests}/{rpd_limit} requests today)."
            )
            raise UsageLimitExceeded(
                provider="Groq",
                metric="RPD",
                tokens_used=day_requests,
                token_limit=rpd_limit,
                model_name=model_name,
                usage_state_path=self.groq_rate_state_path,
            )

        if int(self._groq_rate_state.get("day_tokens", 0)) + tokens_to_spend > tpd_limit:
            self._budget_pause_requested = True
            self._budget_pause_reason = (
                f"Groq daily token limit would be exceeded for '{model_name}' "
                f"({int(self._groq_rate_state.get('day_tokens', 0)) + tokens_to_spend:,}/{tpd_limit:,} estimated tokens today)."
            )
            raise UsageLimitExceeded(
                provider="Groq",
                metric="TPD",
                tokens_used=int(self._groq_rate_state.get("day_tokens", 0)) + tokens_to_spend,
                token_limit=tpd_limit,
                model_name=model_name,
                usage_state_path=self.groq_rate_state_path,
            )

        if minute_tokens + tokens_to_spend > tpm_limit:
            if tokens_to_spend > tpm_limit:
                self._budget_pause_requested = True
                self._budget_pause_reason = (
                    f"Groq TPM limit would still be exceeded for '{model_name}' "
                    f"even after waiting for reset ({tokens_to_spend:,}/{tpm_limit:,} estimated tokens)."
                )
                raise UsageLimitExceeded(
                    provider="Groq",
                    metric="TPM",
                    tokens_used=tokens_to_spend,
                    token_limit=tpm_limit,
                    model_name=model_name,
                    usage_state_path=self.groq_rate_state_path,
                )
            self._groq_wait_for_next_minute(
                f"Groq TPM limit would be exceeded for '{model_name}' "
                f"({minute_tokens + tokens_to_spend:,}/{tpm_limit:,} estimated tokens this minute)."
            )
            return

    def _extract_usage_from_response(self, resp: Any, prompt: str, response_text: str) -> Dict[str, int]:
        usage = None
        if isinstance(resp, dict):
            usage = resp.get("usage")
        else:
            usage = getattr(resp, "usage", None)

        input_tokens = None
        output_tokens = None
        total_tokens = None

        if usage is not None:
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
                output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
                total_tokens = usage.get("total_tokens")
            else:
                input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
                output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
                total_tokens = getattr(usage, "total_tokens", None)

        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = int(input_tokens) + int(output_tokens)

        if total_tokens is None:
            prompt_estimate = max(1, math.ceil(len(prompt) / 4))
            output_estimate = max(1, math.ceil(len(response_text) / 4))
            input_tokens = input_tokens if input_tokens is not None else prompt_estimate
            output_tokens = output_tokens if output_tokens is not None else output_estimate
            total_tokens = int(input_tokens) + int(output_tokens)
        else:
            if input_tokens is None:
                input_tokens = max(1, math.ceil(len(prompt) / 4))
            if output_tokens is None:
                output_tokens = max(1, int(total_tokens) - int(input_tokens))

        return {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": int(total_tokens),
            "estimated": int(usage is None),
        }

    def _record_openai_usage(self, model_name: str, usage: Dict[str, int]) -> Dict[str, Any]:
        self._reset_usage_state_if_needed(self._usage_state)
        bucket = self._bucket_for_model(model_name)
        bucket_state = self._usage_state["buckets"].setdefault(
            bucket,
            {
                "tokens": 0,
                "limit": int(self.openai_daily_token_limits.get(bucket, 0)),
                "models": {},
            },
        )
        bucket_state["limit"] = int(self.openai_daily_token_limits.get(bucket, bucket_state.get("limit", 0)))
        bucket_state["tokens"] = int(bucket_state.get("tokens", 0)) + int(usage["total_tokens"])
        bucket_models = bucket_state.setdefault("models", {})
        bucket_models[model_name] = int(bucket_models.get(model_name, 0)) + int(usage["total_tokens"])

        exceeded = bucket_state["tokens"] > bucket_state["limit"]
        self._usage_state["paused"] = exceeded
        self._usage_state["pause_reason"] = (
            f"{bucket} budget exceeded for {model_name}"
            if exceeded
            else ""
        )
        self._save_usage_state()

        if exceeded:
            self._budget_pause_requested = True
            self._budget_pause_reason = (
                f"OpenAI {bucket} budget exceeded for '{model_name}' "
                f"({bucket_state['tokens']:,}/{bucket_state['limit']:,} tokens today)."
            )

        return {
            "bucket": bucket,
            "used": int(bucket_state["tokens"]),
            "limit": int(bucket_state["limit"]),
            "exceeded": exceeded,
        }

    def _record_groq_usage(self, model_name: str, usage: Dict[str, int]) -> Dict[str, Any]:
        self._reset_groq_rate_state_if_needed(self._groq_rate_state)
        tpm_limit = self._groq_limit_value("tpm")
        rpm_limit = self._groq_limit_value("rpm")
        rpd_limit = self._groq_limit_value("rpd")
        tokens = int(usage["total_tokens"])

        self._groq_rate_state["minute_tokens"] = int(self._groq_rate_state.get("minute_tokens", 0)) + tokens
        self._groq_rate_state["minute_requests"] = int(self._groq_rate_state.get("minute_requests", 0)) + 1
        self._groq_rate_state["day_tokens"] = int(self._groq_rate_state.get("day_tokens", 0)) + tokens
        self._groq_rate_state["day_requests"] = int(self._groq_rate_state.get("day_requests", 0)) + 1

        models = self._groq_rate_state.setdefault("models", {})
        model_state = models.setdefault(
            model_name,
            {"requests": 0, "tokens": 0},
        )
        model_state["requests"] = int(model_state.get("requests", 0)) + 1
        model_state["tokens"] = int(model_state.get("tokens", 0)) + tokens

        exceeded = (
            int(self._groq_rate_state["minute_tokens"]) > tpm_limit
            or int(self._groq_rate_state["minute_requests"]) > rpm_limit
            or int(self._groq_rate_state["day_requests"]) > rpd_limit
            or int(self._groq_rate_state["day_tokens"]) > int(self.groq_daily_token_limit)
        )
        self._groq_rate_state["paused"] = exceeded
        self._groq_rate_state["pause_reason"] = (
            f"Groq rate limit exceeded for {model_name}"
            if exceeded
            else ""
        )
        self._save_groq_rate_state()

        if exceeded:
            self._budget_pause_requested = True
            self._budget_pause_reason = (
                f"Groq rate limit exceeded for '{model_name}' "
                f"(minute tokens {self._groq_rate_state['minute_tokens']:,}/{tpm_limit:,}, "
                f"minute requests {self._groq_rate_state['minute_requests']:,}/{rpm_limit:,}, "
                f"day requests {self._groq_rate_state['day_requests']:,}/{rpd_limit:,}, "
                f"daily tokens {self._groq_rate_state['day_tokens']:,}/{int(self.groq_daily_token_limit):,})."
            )

        return {
            "minute_tokens": int(self._groq_rate_state["minute_tokens"]),
            "minute_requests": int(self._groq_rate_state["minute_requests"]),
            "day_tokens": int(self._groq_rate_state["day_tokens"]),
            "day_requests": int(self._groq_rate_state["day_requests"]),
            "tpm_limit": tpm_limit,
            "rpm_limit": rpm_limit,
            "rpd_limit": rpd_limit,
            "tpd_limit": int(self.groq_daily_token_limit),
            "exceeded": exceeded,
        }

    def _parse_groq_rate_limit_error(self, error_text: str) -> Dict[str, Any]:
        normalized = error_text.lower()
        if "rate_limit_exceeded" not in normalized and "rate limit reached" not in normalized:
            return {}

        info: Dict[str, Any] = {
            "metric": "",
            "tokens_used": None,
            "token_limit": None,
            "requested": None,
            "retry_after": None,
        }

        if "tokens per day" in normalized or "(tpd)" in normalized:
            info["metric"] = "TPD"
        elif "tokens per minute" in normalized or "(tpm)" in normalized:
            info["metric"] = "TPM"
        elif "requests per minute" in normalized or "(rpm)" in normalized:
            info["metric"] = "RPM"
        else:
            info["metric"] = "TPD"

        match = re.search(r"Limit\s+(\d+),\s+Used\s+(\d+),\s+Requested\s+(\d+)", error_text, re.IGNORECASE)
        if match:
            info["token_limit"] = int(match.group(1))
            info["tokens_used"] = int(match.group(2))
            info["requested"] = int(match.group(3))

        retry_after = re.search(r"try again in\s+([0-9.]+)s", error_text, re.IGNORECASE)
        if retry_after:
            try:
                info["retry_after"] = max(1, int(math.ceil(float(retry_after.group(1)))))
            except ValueError:
                pass

        return info

    def has_budget_pause(self) -> bool:
        if self.provider not in ("openai", "groq"):
            return False
        if self.provider == "groq":
            return bool(self._budget_pause_requested or self._groq_rate_state.get("paused", False))
        return bool(self._budget_pause_requested or self._usage_state.get("paused", False))

    def get_budget_pause_message(self) -> str:
        if self.provider == "groq":
            if self._budget_pause_reason:
                return self._budget_pause_reason
            if self._groq_rate_state.get("paused"):
                return self._groq_rate_state.get("pause_reason", "") or "Groq rate limit has been exceeded."
            return ""

        if self.provider != "openai":
            return ""

        if self._budget_pause_reason:
            return self._budget_pause_reason

        if self._usage_state.get("paused"):
            pause_reason = self._usage_state.get("pause_reason", "")
            return pause_reason or "OpenAI daily token budget has been exceeded."

        return ""

    def get_budget_status(self) -> Dict[str, Any]:
        if self.provider == "groq":
            self._reset_groq_rate_state_if_needed(self._groq_rate_state)
            return self._groq_rate_state

        self._reset_usage_state_if_needed(self._usage_state)
        return self._usage_state

    def _init_client(self) -> None:
        self.client = None
        self.session = None

        if self.provider in ("openai", "groq") and self.use_openai_client and _HAS_OPENAI_CLIENT:
            try:
                self.client = OpenAI(
                    api_key=self.api_key, base_url=self.base_url
                )
                if self.provider == "groq":
                    print("Using OpenAI-compatible client for Groq")
                else:
                    print("Using OpenAI Python client")
                return
            except Exception as e:
                print(f"{self.provider.title()} client initialization failed, falling back to HTTP. Error:", e)

        if self.provider == "google" and _HAS_GEMINI_CLIENT:
            try:
                self.client = genai.Client(api_key=self.api_key)
                print("Using Google Gemini client")
                return
            except Exception as e:
                print("Gemini client initialization failed. Error:", e)

        # Fallback: HTTP session for OpenAI-style APIs
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
        )
        print("Using HTTP session for requests to:", self.base_url)

    def _extract_text_from_response(self, resp: Any) -> str:
        """
        Normalize different response shapes to a text string.
        """
        # OpenAI Python client convenience property (if present)
        try:
            if hasattr(resp, "output_text"):
                return str(resp.output_text)
            if hasattr(resp, "output") and isinstance(resp.output, list) and resp.output:
                parts = []
                for item in resp.output:
                    if isinstance(item, dict) and "content" in item and isinstance(item["content"], list):
                        for c in item["content"]:
                            if isinstance(c, dict) and "text" in c:
                                parts.append(c["text"])
                    elif isinstance(item, str):
                        parts.append(item)
                if parts:
                    return "\n".join(parts)
        except Exception:
            pass

        # Chat completion-style object responses
        try:
            choices = getattr(resp, "choices", None)
            if choices:
                parts = []
                for choice in choices:
                    message = getattr(choice, "message", None)
                    if message is None and isinstance(choice, dict):
                        message = choice.get("message")

                    content = getattr(message, "content", None)
                    if content is None and isinstance(message, dict):
                        content = message.get("content")

                    if isinstance(content, str):
                        parts.append(content)
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and "text" in item:
                                parts.append(item["text"])
                            else:
                                parts.append(str(item))
                if parts:
                    return "\n".join(parts)
        except Exception:
            pass

        # Gemini response
        try:
            if hasattr(resp, "text"):
                return str(resp.text)
        except Exception:
            pass

        # HTTP response JSON
        try:
            if isinstance(resp, dict):
                if "output" in resp:
                    out = resp["output"]
                    if isinstance(out, list):
                        texts = []
                        for o in out:
                            if isinstance(o, dict) and "content" in o and isinstance(o["content"], list):
                                for c in o["content"]:
                                    if isinstance(c, dict) and "text" in c:
                                        texts.append(c["text"])
                            elif isinstance(o, str):
                                texts.append(o)
                        if texts:
                            return "\n".join(texts)
                if "choices" in resp and isinstance(resp["choices"], list) and resp["choices"]:
                    first = resp["choices"][0]
                    if "message" in first and isinstance(first["message"], dict) and "content" in first["message"]:
                        content = first["message"]["content"]
                        if isinstance(content, str):
                            return content
                        if isinstance(content, list):
                            return "\n".join(
                                [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
                            )
                    if "text" in first:
                        return first["text"]
        except Exception:
            pass

        return ""

    def _default_completion_tokens(self, model_type: str) -> int:
        if self.provider == "groq":
            defaults = {
                "writing": int(self.config.get("groq_writing_completion_tokens", 3072)),
                "review": int(self.config.get("groq_review_completion_tokens", 1536)),
                "planning": int(self.config.get("groq_planning_completion_tokens", 1024)),
            }
            return max(256, defaults.get(model_type, int(self.config.get("groq_default_completion_tokens", 2048))))

        defaults = {
            "writing": int(self.config.get("writing_completion_tokens", 4096)),
            "review": int(self.config.get("review_completion_tokens", 2048)),
            "planning": int(self.config.get("planning_completion_tokens", 1024)),
        }
        return max(256, defaults.get(model_type, int(self.config.get("default_completion_tokens", 2048))))

    def generate_content(
        self,
        prompt: str,
        model_type: str = "writing",
        max_retries: int = 5,
        max_completion_tokens: Optional[int] = None,
    ) -> str:
        retry_delays = [1, 2, 4, 8, 16]
        attempt = 0
        last_error = None
        request_prompt = prompt

        model_to_use = self.writing_model if model_type == "writing" else self.review_model
        completion_tokens = max_completion_tokens or self._default_completion_tokens(model_type)
        payload = {"model": model_to_use, "input": request_prompt}

        while attempt < max_retries:
            try:
                if self.provider == "groq":
                    self._groq_preflight_limit_check(model_to_use, request_prompt)
                if self.provider == "openai":
                    self._reset_usage_state_if_needed(self._usage_state)
                    bucket = self._bucket_for_model(model_to_use)
                    bucket_state = self._usage_state["buckets"].get(bucket, {})
                    if int(bucket_state.get("tokens", 0)) > int(bucket_state.get("limit", 0)):
                        self._budget_pause_requested = True
                        raise DailyTokenBudgetExceeded(
                            bucket=bucket,
                            tokens_used=int(bucket_state.get("tokens", 0)),
                            token_limit=int(bucket_state.get("limit", 0)),
                            model_name=model_to_use,
                            usage_state_path=self.usage_state_path,
                        )

                if self.provider == "google" and self.client is not None:
                    # Gemini API
                    try:
                        resp = self.client.models.generate_content(
                            model=model_to_use, contents=request_prompt
                        )
                    except Exception as e:
                        last_error = e
                        print(f"[gemini] client call failed on attempt {attempt + 1}: {e}")
                        raise
                    text = self._extract_text_from_response(resp)
                    if text and text.strip():
                        return text
                    print(f"[gemini] returned empty content on attempt {attempt + 1}")

                elif self.client is not None and self.provider == "openai":
                    # OpenAI Python client
                    try:
                        client_kwargs = {
                            "model": model_to_use,
                            "input": request_prompt,
                            "timeout": self.timeout,
                        }
                        if self.provider == "openai":
                            client_kwargs["service_tier"] = "flex"
                        resp = self.client.responses.create(**client_kwargs)
                    except Exception as e:
                        last_error = e
                        print(f"[{self.provider}_client] client call failed on attempt {attempt + 1}: {e}")
                        raise
                    text = self._extract_text_from_response(resp)
                    if text and text.strip():
                        usage = self._extract_usage_from_response(resp, prompt, text)
                        if self.provider == "openai":
                            budget_info = self._record_openai_usage(model_to_use, usage)
                            if budget_info["exceeded"]:
                                print(
                                    f"⚠️ OpenAI {budget_info['bucket']} usage is now "
                                    f"{budget_info['used']:,}/{budget_info['limit']:,} tokens today. "
                                    "Progress has been cached and the next OpenAI request will pause."
                                )
                        return text
                    print(f"[{self.provider}_client] returned empty content on attempt {attempt + 1}")

                elif self.client is not None and self.provider == "groq":
                    try:
                        resp = self.client.chat.completions.create(
                            model=model_to_use,
                            messages=[{"role": "user", "content": request_prompt}],
                            max_completion_tokens=completion_tokens,
                            timeout=self.timeout,
                        )
                    except Exception as e:
                        last_error = e
                        print(f"[groq_client] client call failed on attempt {attempt + 1}: {e}")
                        raise
                    text = self._extract_text_from_response(resp)
                    if text and text.strip():
                        usage = self._extract_usage_from_response(resp, prompt, text)
                        rate_info = self._record_groq_usage(model_to_use, usage)
                        if rate_info["exceeded"]:
                            print(
                                f"⚠️ Groq rate usage is now minute tokens {rate_info['minute_tokens']:,}/{rate_info['tpm_limit']:,}, "
                                f"minute requests {rate_info['minute_requests']:,}/{rate_info['rpm_limit']:,}, "
                                f"day requests {rate_info['day_requests']:,}/{rate_info['rpd_limit']:,}. "
                                "Progress has been cached and the next Groq request will pause."
                            )
                        return text
                    print(f"[groq_client] returned empty content on attempt {attempt + 1}")

                else:
                    # HTTP endpoint (OpenAI-compatible)
                    if self.session is None:
                        raise RuntimeError("HTTP session is not initialized")

                    if self.provider == "groq":
                        url = self.base_url.rstrip("/") + "/chat/completions"
                        payload = {
                            "model": model_to_use,
                            "messages": [{"role": "user", "content": request_prompt}],
                            "max_completion_tokens": completion_tokens,
                        }
                    else:
                        payload = {"model": model_to_use, "input": request_prompt}
                        url = self.base_url.rstrip("/") + "/responses"
                    try:
                        r = self.session.post(url, json=payload, timeout=self.timeout)
                    except requests.exceptions.RequestException as e:
                        last_error = e
                        print(f"[http] request failed on attempt {attempt + 1}: {e}")
                        raise

                    if r.status_code == 200:
                        try:
                            data = r.json()
                        except ValueError:
                            print(f"[http] response not JSON on attempt {attempt + 1}")
                            data = {}
                        text = self._extract_text_from_response(data)
                        if text and text.strip():
                            usage = self._extract_usage_from_response(data, prompt, text)
                            if self.provider == "openai":
                                budget_info = self._record_openai_usage(model_to_use, usage)
                                if budget_info["exceeded"]:
                                    print(
                                        f"⚠️ OpenAI {budget_info['bucket']} usage is now "
                                        f"{budget_info['used']:,}/{budget_info['limit']:,} tokens today. "
                                        "Progress has been cached and the next OpenAI request will pause."
                                    )
                            elif self.provider == "groq":
                                rate_info = self._record_groq_usage(model_to_use, usage)
                                if rate_info["exceeded"]:
                                    print(
                                        f"⚠️ Groq rate usage is now minute tokens {rate_info['minute_tokens']:,}/{rate_info['tpm_limit']:,}, "
                                        f"minute requests {rate_info['minute_requests']:,}/{rate_info['rpm_limit']:,}, "
                                        f"day requests {rate_info['day_requests']:,}/{rate_info['rpd_limit']:,}. "
                                        "Progress has been cached and the next Groq request will pause."
                                    )
                            return text
                        print(f"[http] returned empty content on attempt {attempt + 1}")
                    else:
                        if r.status_code in (429, 502, 503, 504):
                            if self.provider == "groq":
                                groq_rate_limit = self._parse_groq_rate_limit_error(r.text)
                                if groq_rate_limit and groq_rate_limit.get("metric") == "TPD":
                                    tokens_used = int(groq_rate_limit.get("tokens_used") or 0)
                                    token_limit = int(groq_rate_limit.get("token_limit") or self.groq_daily_token_limit)
                                    self._budget_pause_requested = True
                                    self._budget_pause_reason = (
                                        f"Groq TPD limit reached for '{model_to_use}' "
                                        f"({tokens_used:,}/{token_limit:,} tokens today)."
                                    )
                                    raise UsageLimitExceeded(
                                        provider="Groq",
                                        metric="TPD",
                                        tokens_used=tokens_used,
                                        token_limit=token_limit,
                                        model_name=model_to_use,
                                        usage_state_path=self.groq_rate_state_path,
                                    )
                            print(f"[http] transient HTTP error {r.status_code} - will retry (attempt {attempt + 1})")
                        else:
                            print(f"[http] HTTP error {r.status_code}: {r.text}")
                            r.raise_for_status()

            except (DailyTokenBudgetExceeded, UsageLimitExceeded):
                raise
            except Exception as e:
                error_text = str(e).lower()
                request_too_large = "request_too_large" in error_text or "request entity too large" in error_text
                if self.provider == "groq":
                    groq_rate_limit = self._parse_groq_rate_limit_error(str(e))
                    if groq_rate_limit:
                        metric = groq_rate_limit.get("metric", "")
                        if metric == "TPD":
                            tokens_used = int(groq_rate_limit.get("tokens_used") or 0)
                            token_limit = int(groq_rate_limit.get("token_limit") or self.groq_daily_token_limit)
                            self._budget_pause_requested = True
                            self._budget_pause_reason = (
                                f"Groq TPD limit reached for '{model_to_use}' "
                                f"({tokens_used:,}/{token_limit:,} tokens today)."
                            )
                            raise UsageLimitExceeded(
                                provider="Groq",
                                metric="TPD",
                                tokens_used=tokens_used,
                                token_limit=token_limit,
                                model_name=model_to_use,
                                usage_state_path=self.groq_rate_state_path,
                            )
                        retry_after = groq_rate_limit.get("retry_after")
                        if retry_after and attempt < max_retries - 1:
                            print(
                                f"[groq] Rate limit reached on attempt {attempt + 1}; "
                                f"waiting {retry_after} seconds before retrying."
                            )
                            time.sleep(int(retry_after))
                            attempt += 1
                            continue
                if request_too_large and attempt < max_retries - 1:
                    print(
                        f"[{self.provider}] Request too large on attempt {attempt + 1}; "
                        "shrinking prompt and retrying."
                    )
                    request_prompt = self._shrink_prompt_text(request_prompt, shrink_factor=0.7)
                    self._budget_pause_requested = False
                    attempt += 1
                    delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    print(f"Waiting {delay} seconds before retry...")
                    time.sleep(delay)
                    continue
                last_error = e
                print(f"[{self.provider}] Error on attempt {attempt + 1}: {e}")

            delay = retry_delays[min(attempt, len(retry_delays)-1)]
            print(f"Waiting {delay} seconds before retry...")
            time.sleep(delay)
            attempt += 1

        print("CRITICAL ERROR: Failed to generate content after all retries")
        if last_error:
            print("Last error:", last_error)
            raise last_error
        raise RuntimeError("CRITICAL ERROR: Failed to generate content after all retries")
