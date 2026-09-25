"""Command-line entry point for creating a book."""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen

# ponytail: load_local_env() runs once in ai_book_creator/__init__.py on import.
from .core.book_creator import AIBookCreator
from .env import exit_on_ctrl_c
from .services.ai_service import ensure_openai_oauth_proxy, load_opencode_go_sync


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

PROVIDER_CONFIG_MAP = {
    "google": str(PACKAGE_ROOT / "config" / "ai_config_google.local.json"),
    "openai": str(PACKAGE_ROOT / "config" / "ai_config_openai.local.json"),
    "openai-oauth": str(PACKAGE_ROOT / "config" / "ai_config_openai_oauth.json"),
    "groq": str(PACKAGE_ROOT / "config" / "ai_config_groq.local.json"),
    "minimax": str(PACKAGE_ROOT / "config" / "ai_config_minimax.local.json"),
    "openrouter": str(PACKAGE_ROOT / "config" / "ai_config_openrouter.json"),
    "opencode-go": str(PACKAGE_ROOT / "config" / "ai_config_opencode_go.json"),
    "opencode-zen": str(PACKAGE_ROOT / "config" / "ai_config_opencode_zen.json"),
    "claude": str(PACKAGE_ROOT / "config" / "ai_config_claude.json"),
    "commandcode": str(PACKAGE_ROOT / "config" / "ai_config_commandcode.json"),
    "hyper": str(PACKAGE_ROOT / "config" / "ai_config_hyper.json"),
    "grok": str(PACKAGE_ROOT / "config" / "ai_config_grok.json"),
    "nvidia": str(PACKAGE_ROOT / "config" / "ai_config_nvidia.json"),
}
# Providers whose model list lives in their config file and is picked at runtime.
CATALOGUE_PROVIDERS = ("opencode-go", "opencode-zen", "claude", "commandcode", "hyper", "grok", "nvidia")
PROJECT_OUTPUT_DIR = REPO_ROOT / "book_output"
PROJECT_STATE_FILE = PROJECT_OUTPUT_DIR / "project_data.json"
PROVIDER_STATE_FILE = REPO_ROOT / "book_output" / "provider_state.json"
PROJECT_ARCHIVE_DIR = PROJECT_OUTPUT_DIR / "archive" / "ebooks"
OPENAI_MODEL_OPTIONS = ("gpt-5.4", "gpt-5.4-mini")

# models.dev publishes each model's context/output limits and list price per 1M
# tokens; opencode keeps a copy of it on disk. Sources are searched in order.
MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_CACHE = Path.home() / ".cache" / "opencode" / "models.json"
MODELS_DEV_SOURCES = {
    "google": ("google",),
    "openai": ("openai",),
    "openai-oauth": ("openai",),
    "groq": ("groq",),
    "minimax": ("minimax",),
    "openrouter": ("openrouter",),
    "opencode-go": ("opencode-go",),
    "opencode-zen": ("opencode",),
    "claude": ("anthropic",),
    # Command Code resells other labs' models; show the maker's price when
    # models.dev has it, else OpenRouter's.
    "commandcode": ("anthropic", "openai", "google", "openrouter"),
    "hyper": ("hyper",),
    "grok": ("xai",),
    "nvidia": ("nvidia",),
}
# Artificial Analysis Intelligence Index per model, cached a day like models.dev.
AA_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
AA_CACHE = Path.home() / ".cache" / "ai-book-creator" / "artificial_analysis.json"
# `cmdc --list-models` takes 5-25 s, so its listing is reused for a day.
CMDC_MODELS_CACHE = Path.home() / ".cache" / "ai-book-creator" / "cmdc_models.txt"
# Slug words that only name a reasoning setting or release channel.
AA_VARIANT_WORDS = frozenset(
    "thinking reasoning nonreasoning non adaptive preview exp low medium high xhigh max minimal".split()
)
# Hosted names AA lists under the open-weights model they serve.
AA_ALIASES = {
    "qwen3.5-plus": "qwen3-5-397b-a17b",
    "qwen3.8-flash": "qwen3-8-flash-next",
    # OpenCode's stealth model; GLM-4.6 by community identification, never confirmed.
    "big-pickle": "glm-4-6",
}
# Paid through a subscription, so the price shown is only the API list rate.
SUBSCRIPTION_PROVIDERS = ("claude", "commandcode", "opencode-go", "openai-oauth")


@lru_cache(maxsize=None)
def _models_dev() -> dict:
    """models.dev catalogue: opencode's copy if under a day old, else live, else stale."""
    try:
        if time.time() - MODELS_DEV_CACHE.stat().st_mtime < 86400:
            return json.loads(MODELS_DEV_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        req = Request(MODELS_DEV_URL, headers={"User-Agent": "ai-book-creator"})
        with urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except Exception:
        pass
    try:
        return json.loads(MODELS_DEV_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=None)
def _artificial_analysis() -> list:
    """Artificial Analysis model list: disk copy if under a day old, else live, else stale.

    Needs a free key in ARTIFICIAL_ANALYSIS_API_KEY; without one, returns [].
    """
    try:
        if time.time() - AA_CACHE.stat().st_mtime < 86400:
            return json.loads(AA_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "").strip()
    if key:
        try:
            req = Request(AA_URL, headers={"x-api-key": key, "User-Agent": "ai-book-creator"})
            with urlopen(req, timeout=10) as resp:
                data = json.load(resp).get("data") or []
            if data:
                AA_CACHE.parent.mkdir(parents=True, exist_ok=True)
                AA_CACHE.write_text(json.dumps(data), encoding="utf-8")
                return data
        except Exception:
            pass
    try:
        return json.loads(AA_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _name_key(name: str) -> tuple[tuple[str, ...], frozenset[str]]:
    """'claude-sonnet-4-5' and 'claude-4-5-sonnet' -> (('4', '5'), {'claude', 'sonnet'}).

    Numbers keep their order (gpt-5.4 is not gpt-4.5); words don't.
    """
    parts = [p for p in re.split(r"[^a-z0-9]+", name.lower()) if p and p not in ("free", "contributor")]
    return (tuple(p for p in parts if p.isdigit()), frozenset(p for p in parts if not p.isdigit()))


def _intelligence(mid: str) -> float | None:
    """Artificial Analysis Intelligence Index for a model id, or None if unmatched."""
    tail = mid.lower().rsplit("/", 1)[-1]
    numbers, words = _name_key(AA_ALIASES.get(tail, tail))
    words -= {"preview", "exp"}  # not "max": gpt-5.1-codex-max is its own model
    best = None
    for entry in _artificial_analysis():
        score = (entry.get("evaluations") or {}).get("artificial_analysis_intelligence_index")
        if not isinstance(score, (int, float)):
            continue
        e_numbers, e_words = _name_key(str(entry.get("slug") or ""))
        # Trailing multi-digit numbers are snapshot dates: mimo-v2-5-0424, grok-build-0-1-06-16.
        dated = e_numbers[len(numbers):]
        if e_numbers[:len(numbers)] != numbers or any(len(n) < 2 for n in dated):
            continue
        if not words <= e_words:
            continue
        # AA slugs may add the maker and parameter count: nvidia-nemotron-3-ultra-550b-a55b.
        maker = str((entry.get("model_creator") or {}).get("slug") or "").lower()
        extra = {w for w in e_words - words if w != maker and not re.fullmatch(r"a?\d+b", w)}
        # ponytail: AA lists one entry per reasoning setting; best one wins.
        if extra <= AA_VARIANT_WORDS:
            best = max(best or 0.0, float(score))
    return best


def _model_facts(provider: str, mid: str) -> dict:
    """models.dev entry ({"limit": ..., "cost": ...}) plus AA "intelligence", or {}."""
    info = _models_dev_facts(provider, mid)
    score = _intelligence(mid)
    return {**info, "intelligence": score} if score is not None else info


def _models_dev_facts(provider: str, mid: str) -> dict:
    mid = mid.lower()
    tail = mid.rsplit("/", 1)[-1]
    for source in MODELS_DEV_SOURCES.get(provider, ()):
        models = (_models_dev().get(source) or {}).get("models") or {}
        # Claude Code aliases ("opus") follow the newest model of that family.
        family = [m for m in models.values() if m.get("family") == f"claude-{mid}"]
        if provider == "claude" and family:
            return max(family, key=lambda m: m.get("release_date", ""))
        by_id = {key.lower(): info for key, info in models.items()}
        by_tail = {key.lower().rsplit("/", 1)[-1]: info for key, info in models.items()}
        if mid in by_id or tail in by_tail:
            return by_id.get(mid) or by_tail[tail]
    return {}


def _tokens(n: int) -> str:
    """1048576 -> '1M', 131072 -> '131K'."""
    return f"{n / 1e6:.3g}M" if n >= 999_500 else f"{round(n / 1e3)}K"


SORT_KEYS = {"price": "price", "ctx": "context", "AA": "intelligence", "score": "aggregate score"}


def _scales(infos: list[dict]) -> list[dict]:
    """Each model's ctx, price and AA scaled 0..1 across the menu (1 = best), plus
    "score": their mean, with a metric the model lacks counted as 0."""
    def raw(info: dict) -> dict:
        ctx = (info.get("limit") or {}).get("context")
        cost = info.get("cost") or {}
        # Logs: context and price span orders of magnitude. Output price is what
        # a book's worth of prose costs.
        return {"ctx": math.log(ctx) if ctx else None,
                "price": -math.log1p(cost["output"]) if "output" in cost else None,
                "AA": info.get("intelligence")}

    raws = [raw(i) for i in infos]
    scaled: list[dict] = [{} for _ in infos]
    present = [k for k in ("ctx", "price", "AA") if any(r[k] is not None for r in raws)]
    for key in present:
        values = [r[key] for r in raws if r[key] is not None]
        lo, hi = min(values), max(values)
        for r, s in zip(raws, scaled):
            if r[key] is not None:
                s[key] = (r[key] - lo) / (hi - lo) if hi > lo else 1.0
    for s in scaled:
        s["score"] = sum(s.values()) / len(present) if present else 0.0
    # Rescale the score too, so its colors also run from the menu's worst to best.
    lo, hi = min((s["score"] for s in scaled), default=0), max((s["score"] for s in scaled), default=0)
    for s in scaled:
        s["score_t"] = (s["score"] - lo) / (hi - lo) if hi > lo else 1.0
    return scaled


def _gradient(text: str, t: float | None) -> str:
    """t 0..1 colors red through orange and yellow to bright green (24-bit).

    The red end stays bright enough to read on a dark console (low vision).
    """
    if t is None:
        return text
    r, g, b = colorsys.hsv_to_rgb(t / 3, 1, 0.8 + 0.2 * t)
    return _color(text, f"38;2;{round(r * 255)};{round(g * 255)};{round(b * 255)}")


def _facts_label(info: dict, scale: dict | None = None) -> str:
    """'ctx 1M | $4/$20 | AA 45 -> 72' — price is $ per 1M input/output tokens.

    scale (from _scales) colors each figure; the arrow and aggregate score need it.
    """
    scale = scale or {}
    context = (info.get("limit") or {}).get("context")
    parts = [_gradient(f"ctx {_tokens(context)}", scale.get("ctx")) if context else "ctx ?"]
    cost = info.get("cost") or {}
    if "input" in cost and "output" in cost:
        free = not (cost["input"] or cost["output"])
        price = "free" if free else f"${cost['input']:.3g}/${cost['output']:.3g}"
        parts.append(_gradient(price, scale.get("price")))
    else:
        parts.append("$?")
    if _artificial_analysis():
        score = info.get("intelligence")
        parts.append(_gradient(f"AA {score:.0f}", scale.get("AA")) if score is not None else "AA ?")
    label = " | ".join(parts)
    if "score" in scale:
        label += " -> " + _gradient(f"{scale['score'] * 100:.0f}", scale["score_t"])
    return label


def _cost_key(info: dict) -> tuple:
    """Menu sort: free first, then output price, then input; unpriced last."""
    cost = info.get("cost") or {}
    if "input" not in cost or "output" not in cost:
        return (1, 0, 0)
    return (0, cost["output"], cost["input"])


def _color(text: str, code: str) -> str:
    """ANSI-colored text on a terminal; plain when piped or NO_COLOR is set."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    os.system("")  # turns on ANSI escape handling in the Windows console
    return f"\033[{code}m{text}\033[0m"


def _live_model_ids(provider: str) -> set[str] | None:
    """Model ids the provider serves right now, or None when it can't be asked."""
    try:
        if provider == "commandcode":
            try:
                fresh = time.time() - CMDC_MODELS_CACHE.stat().st_mtime < 86400
            except OSError:
                fresh = False
            if fresh:
                listing = CMDC_MODELS_CACHE.read_text(encoding="utf-8")
            else:
                listing = subprocess.run(
                    [shutil.which("cmdc") or "cmdc", "--list-models"],
                    capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
                ).stdout
                if listing.strip():
                    CMDC_MODELS_CACHE.parent.mkdir(parents=True, exist_ok=True)
                    CMDC_MODELS_CACHE.write_text(listing, encoding="utf-8")
            return {line.split()[0].lower() for line in listing.splitlines() if line.strip()} or None
        with open(PROVIDER_CONFIG_MAP[provider], "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("base_url"):
            return None
        # opencode's CDN answers urllib's default User-Agent with a 403.
        req = Request(data["base_url"].rstrip("/") + "/models",
                      headers={"User-Agent": "ai-book-creator"})
        key = os.environ.get(data.get("api_key_env", ""), "")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urlopen(req, timeout=5) as resp:
            return {str(m["id"]).lower() for m in json.load(resp)["data"]}
    except Exception:
        return None


def _load_catalogue(provider: str) -> dict:
    # Local config first (carries friendly display names like "GLM-5.2").
    out: dict = {}
    try:
        with open(PROVIDER_CONFIG_MAP[provider], "r", encoding="utf-8") as f:
            data = json.load(f)
        for mid, info in (data.get("models") or {}).items():
            out[str(mid).lower()] = [str(info.get("name", mid)), int(info.get("max_output", 4096))]
    except Exception:
        pass
    # Then overlay the output limits from opencode's userspace config for the
    # curated opencode-go models; the live listing below decides what exists.
    if provider == "opencode-go":
        for mid, info in load_opencode_go_sync().get("models", {}).items():
            if mid in out:
                out[mid][1] = int(info["max_output"])
    # Drop curated ids the provider has retired, so the menu never offers a
    # dead model. Offline or unreachable: keep the curated list as is.
    live = _live_model_ids(provider) if provider != "claude" else None
    if live and any(mid in live for mid in out):
        out = {mid: entry for mid, entry in out.items() if mid in live}
        # And offer what the provider added since the list was curated, when
        # models.dev can say how much it writes (skips image/embedding ids).
        for mid in sorted(live - set(out)):
            max_out = int((_model_facts(provider, mid).get("limit") or {}).get("output") or 0)
            if max_out:
                out[mid] = [mid, max_out]
    if not out:
        fallback = (
            {"deepseek-v4-flash-free": ["DeepSeek V4 Flash Free", 128000]}
            if provider == "opencode-zen"
            else {"glm-5.2": ["GLM-5.2", 131072]}
        )
        out = fallback
    return out


_CATALOGUE_CACHE: dict[str, dict] = {}


def _provider_models(provider: str) -> dict:
    """{model id: [display name, max output tokens]} for a catalogue provider."""
    if provider not in _CATALOGUE_CACHE:
        _CATALOGUE_CACHE[provider] = _load_catalogue(provider)
    return _CATALOGUE_CACHE[provider]


def _model_state_key(provider: str) -> str:
    return f"{provider.replace('-', '_')}_model"

# ponytail: shared by _has_previous_generated_artifacts and _clear_project_output.
PROJECT_ARTIFACT_PATTERNS = (
    "project_data.json",
    "glossary.json",
    "book_analysis.txt",
    "book_glossary.txt",
    "checkpoint_*.json",
    "chapter_*.txt",
    "models_used.json",
)


def _load_provider_state(state_file: Path | None = None) -> dict:
    state_file = state_file or PROVIDER_STATE_FILE
    try:
        if state_file.exists():
            with state_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _load_last_provider(default_provider: str = "google", state_file: Path | None = None) -> str:
    data = _load_provider_state(state_file)
    provider = str(data.get("provider", "")).lower()
    if provider in PROVIDER_CONFIG_MAP:
        return provider
    return default_provider


def _default_openai_model() -> str:
    try:
        base_path = Path(PROVIDER_CONFIG_MAP["openai"])
        local_path = base_path.with_name(base_path.stem + ".local.json")
        path_to_load = local_path if local_path.exists() else base_path
        
        with open(path_to_load, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            candidate = str(data.get("writing_model") or data.get("review_model") or "").lower()
            if candidate in OPENAI_MODEL_OPTIONS:
                return candidate
    except Exception:
        pass
    return "gpt-5.4-mini"


def _load_last_openai_model(
    default_model: str | None = None,
    options: tuple[str, ...] = OPENAI_MODEL_OPTIONS,
    state_key: str = "openai_model",
    state_file: Path | None = None,
) -> str:
    data = _load_provider_state(state_file)
    model = str(data.get(state_key, "")).lower()
    if model in options:
        return model
    fallback = default_model or _default_openai_model()
    return fallback if fallback in options else options[0]


def _save_last_provider(provider: str, openai_model: str | None = None,
                        catalogue_model: str | None = None,
                        state_file: Path | None = None, extra: dict | None = None) -> None:
    state_file = state_file or PROVIDER_STATE_FILE
    data = _load_provider_state(state_file)
    provider = provider.lower()
    data["provider"] = provider
    if openai_model is not None:
        data["openai_oauth_model" if provider == "openai-oauth" else "openai_model"] = openai_model.lower()
    elif provider == "openai" and str(data.get("openai_model", "")).lower() not in OPENAI_MODEL_OPTIONS:
        data["openai_model"] = _default_openai_model()
    if provider in CATALOGUE_PROVIDERS:
        key = _model_state_key(provider)
        models = _provider_models(provider)
        if catalogue_model is not None:
            data[key] = catalogue_model.lower()
        elif str(data.get(key, "")).lower() not in models:
            data[key] = next(iter(models))
    data.update(extra or {})

    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _prompt_provider(default_provider: str) -> str:
    valid_providers = list(PROVIDER_CONFIG_MAP.keys())
    prompt = f"Choose provider ({', '.join(valid_providers)}) [default: {default_provider}]: "

    while True:
        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            return default_provider
        if not choice:
            return default_provider
        if choice in PROVIDER_CONFIG_MAP:
            return choice
        print(f"Invalid provider. Please choose one of: {', '.join(valid_providers)}")


def _normalize_openai_model(choice: str) -> str:
    normalized = choice.strip().lower()
    aliases = {
        "1": "gpt-5.4",
        "gpt-5.4": "gpt-5.4",
        "5.4": "gpt-5.4",
        "2": "gpt-5.4-mini",
        "gpt-5.4-mini": "gpt-5.4-mini",
        "mini": "gpt-5.4-mini",
    }
    return aliases.get(normalized, "")


def _load_openai_oauth_models() -> dict[str, tuple[int | None, int | None]]:
    ensure_openai_oauth_proxy()
    with urlopen("http://127.0.0.1:10531/v1/models", timeout=10) as response:
        payload = json.load(response)
    models: dict[str, tuple[int | None, int | None]] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        model_id = str(item.get("id", "")).strip() if isinstance(item, dict) else ""
        if not model_id or "image" in model_id.lower():
            continue
        context = item.get("context_window")
        output = item.get("max_output_tokens")
        models[model_id] = (
            int(context) if isinstance(context, int) and context > 0 else None,
            int(output) if isinstance(output, int) and output > 0 else None,
        )
    if not models:
        raise RuntimeError("OpenAI OAuth returned no text models from /v1/models.")
    return models


def _prompt_openai_model(
    default_model: str,
    model_info: dict[str, tuple[int | None, int | None]] | None = None,
    role: str = "",
) -> str:
    options = tuple(model_info) if model_info is not None else OPENAI_MODEL_OPTIONS
    rows = []
    for model in options:
        context, output = model_info.get(model, (None, None)) if model_info is not None else (None, None)
        info = _model_facts("openai", model)
        # The OAuth endpoint's own figures win over models.dev when it reports them.
        reported = {k: v for k, v in (("context", context), ("output", output)) if v}
        rows.append((model, {**info, "limit": {**(info.get("limit") or {}), **reported}}))
    title = "Available OpenAI OAuth text models (live):" if model_info is not None else "Available OpenAI models:"
    paid_by = "; your ChatGPT subscription pays" if model_info is not None else ""
    return _pick_model(title, rows, default_model, role, paid_by,
                       None if model_info is not None else _normalize_openai_model)


def _read_choice(prompt: str, on_tab) -> str:
    """input(), except on a Windows console Tab calls on_tab() and keeps reading."""
    if os.name != "nt" or not sys.stdin.isatty():
        # ponytail: no Tab re-sort off Windows; termios raw mode if that's ever needed.
        return input(prompt)
    import msvcrt
    typed = ""
    print(prompt, end="", flush=True)
    while True:
        ch = msvcrt.getwch()
        if ch in "\r\n":
            print()
            return typed
        if ch == "\t":
            on_tab()
            print(prompt + typed, end="", flush=True)
        elif ch == "\x03":
            raise KeyboardInterrupt
        elif ch == "\x1a":
            raise EOFError
        elif ch == "\x08":
            if typed:
                typed = typed[:-1]
                print("\b \b", end="", flush=True)
        elif ch in "\x00\xe0":
            msvcrt.getwch()  # arrow/function key: second half of its code
        elif ch.isprintable():
            typed += ch
            print(ch, end="", flush=True)


def _pick_model(title: str, rows: list[tuple[str, dict]], default_model: str, role: str,
                paid_by: str, normalize=None) -> str:
    """Numbered model menu; Tab re-sorts it by the next of SORT_KEYS, best first."""
    ids = [mid for mid, _ in sorted(rows, key=lambda row: _cost_key(row[1]))]
    infos = dict(rows)
    scales = dict(zip(infos, _scales(list(infos.values()))))
    width = max(map(len, ids))
    state = {"sort": 0, "order": ids, "below": 0}

    def render() -> None:
        key = list(SORT_KEYS)[state["sort"]]
        # Stable over the price order, so ties stay cheapest first; unknowns go last.
        state["order"] = sorted(ids, key=lambda mid: -scales[mid].get(key, -1))
        print(f"{title}  [sorted by {SORT_KEYS[key]}; Tab: next]")
        for i, mid in enumerate(state["order"], 1):
            marker = " (default)" if mid == default_model else ""
            print(f"  {i:2d}. {mid:<{width}}  {_facts_label(infos[mid], scales[mid])}{marker}")
        print(f"  $ = API list price per 1M in/out tokens{paid_by}; -> = aggregate score 0-100.")

    def next_sort() -> None:
        state["sort"] = (state["sort"] + 1) % len(SORT_KEYS)
        # Back to the title line, clear everything below it, draw again.
        print(f"\r\033[{len(ids) + 2 + state['below']}A\033[J", end="")
        state["below"] = 0
        render()

    os.system("")  # ANSI cursor codes on the Windows console
    render()
    prompt = f"Choose {role + ' ' if role else ''}model number or id [default: {default_model}]: "
    while True:
        try:
            choice = _read_choice(prompt, next_sort).strip()
        except EOFError:
            return default_model
        if not choice:
            return default_model
        order = state["order"]
        if choice.isdigit() and 1 <= int(choice) <= len(order):
            return order[int(choice) - 1]
        for model in (choice.lower(), normalize(choice) if normalize else None):
            if model in infos:
                return model
        print(f"Choose a number from 1 to {len(order)} or a listed model id.")
        state["below"] += 2  # the answered prompt line and this one


def _default_catalogue_model(provider: str) -> str:
    models = _provider_models(provider)
    try:
        with open(PROVIDER_CONFIG_MAP[provider], "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            candidate = str(data.get("writing_model") or "").lower()
            if candidate in models:
                return candidate
    except Exception:
        pass
    return next(iter(models))


def _load_last_catalogue_model(provider: str, default_model: str | None = None,
                               state_key: str | None = None, state_file: Path | None = None) -> str:
    models = _provider_models(provider)
    data = _load_provider_state(state_file)
    model = str(data.get(state_key or _model_state_key(provider), "")).lower()
    if model in models:
        return model
    if default_model and default_model.lower() in models:
        return default_model.lower()
    return _default_catalogue_model(provider)


PROVIDER_LABELS = {
    "opencode-go": "OpenCode Go",
    "opencode-zen": "OpenCode Zen (through the OpenCode CLI)",
    "claude": "Claude Code (your subscription, no API key)",
    "commandcode": "Command Code (your subscription, no API key)",
    "hyper": "hyper.charm.land",
    "grok": "xAI Grok",
}


def _prompt_catalogue_model(provider: str, default_model: str, role: str = "") -> str:
    models = _provider_models(provider)
    label = PROVIDER_LABELS.get(provider, provider)
    rows = [(mid, _model_facts(provider, mid)) for mid in models]
    paid_by = "; your subscription pays" if provider in SUBSCRIPTION_PROVIDERS else ""
    return _pick_model(f"Available {label} models:", rows, default_model, role, paid_by)


def _prompt_resume_existing_project() -> bool:
    """Ask whether to resume the current project or start over."""
    prompt = "Existing project found. Resume it? [Y/n]: "
    while True:
        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            return True

        if choice in ("", "y", "yes"):
            return True
        if choice in ("n", "no"):
            return False
        print("Please answer yes or no.")


def _prompt_stash_previous_ebooks() -> bool:
    prompt = "Stash existing EPUBs and cover prompts before starting fresh? [Y/n]: "
    while True:
        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            return True

        if choice in ("", "y", "yes"):
            return True
        if choice in ("n", "no"):
            return False
        print("Please answer yes or no.")


def _collect_previous_ebook_files() -> list[Path]:
    if not PROJECT_OUTPUT_DIR.exists():
        return []

    files: list[Path] = []
    for pattern in ("*.epub", "*_cover_prompt.txt", "*_cover.jpg", "*_kdp.json", "*_KDP_CHECKLIST.txt"):
        for path in PROJECT_OUTPUT_DIR.rglob(pattern):
            try:
                relative = path.relative_to(PROJECT_OUTPUT_DIR)
            except ValueError:
                continue
            if "archive" not in relative.parts:
                files.append(path)
    return files


def _has_previous_generated_artifacts() -> bool:
    if _collect_previous_ebook_files():
        return True

    for pattern in PROJECT_ARTIFACT_PATTERNS:
        if any(PROJECT_OUTPUT_DIR.glob(pattern)):
            return True
    return False


def _unique_target_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _stash_previous_ebook_files() -> list[Path]:
    files = _collect_previous_ebook_files()
    if not files:
        return []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stash_dir = PROJECT_ARCHIVE_DIR / timestamp
    stash_dir.mkdir(parents=True, exist_ok=True)

    moved: list[Path] = []
    for path in files:
        target = _unique_target_path(stash_dir, path.name)
        shutil.move(str(path), str(target))
        moved.append(target)
    return moved


def _clear_project_output() -> None:
    """Remove generated project artifacts so a new run starts cleanly."""
    removed_files: list[str] = []

    for pattern in PROJECT_ARTIFACT_PATTERNS:
        for path in PROJECT_OUTPUT_DIR.glob(pattern):
            try:
                path.unlink()
                removed_files.append(path.name)
            except FileNotFoundError:
                continue

    if removed_files:
        print("Starting a fresh project. Removed previous generated files:")
        for name in sorted(set(removed_files)):
            print(f"  - {name}")
    else:
        print("Starting a fresh project.")


def _prepare_fresh_start() -> None:
    """Offer to archive old ebook files, then clear cached project artifacts."""
    previous_ebook_files = _collect_previous_ebook_files()
    if previous_ebook_files:
        if _prompt_stash_previous_ebooks():
            moved = _stash_previous_ebook_files()
            if moved:
                print("Archived previous ebook files:")
                for path in moved:
                    print(f"  - {path}")
        else:
            print("Leaving existing EPUBs in place.")

    _clear_project_output()


def provider_config_path(provider: str) -> str:
    """Config file AIService should load for a provider: the user's .local.json copy when present.

    Consumers that choose the provider themselves (book-watch's report picker, lamplight's
    wizard, the calibre plugin) call this instead of the interactive choose_ai menu.
    """
    provider = "openai-oauth" if provider == "codex" else provider
    base = Path(PROVIDER_CONFIG_MAP[provider])
    local = base.with_name(base.stem + ".local.json")
    return str(local if local.exists() else base)


def choose_ai(
    provider: str | None = None,
    mode: str = "review",
    state_file: Path | None = None,
    roles: tuple[str, ...] = ("writing",),
    defaults: tuple[str, ...] = (),
    default_provider: str = "google",
) -> tuple[str, str, list[str]]:
    """Provider and model menu shared by every script that uses AIService.

    Book writer, mathforge and music writer all call this one function, so a
    provider or model added here, or newly served by a provider, shows up in
    each of them. Asks for the provider unless one is given, then one model per
    role (the first role writes, the last reviews). mode="auto" asks nothing and
    reuses the picks remembered in state_file (default: book writer's).
    Exports AI_CONFIG_PATH, the role models and completion caps; returns
    (provider, config path, models).
    """
    if provider is None:
        last = _load_last_provider(default_provider, state_file)
        provider = last if mode == "auto" else _prompt_provider(last)
    provider = "openai-oauth" if provider == "codex" else provider
    config_path = provider_config_path(provider)
    if config_path.endswith(".local.json"):
        print(f"Loaded local configuration: {Path(config_path).name}")
    os.environ["AI_CONFIG_PATH"] = config_path

    models: list[str] = []
    state_keys: list[str] = []
    label = lambda role: role if len(roles) > 1 else ""
    if provider in ("openai", "openai-oauth"):
        model_info = _load_openai_oauth_models() if provider == "openai-oauth" else None
        options = tuple(model_info) if model_info is not None else OPENAI_MODEL_OPTIONS
        base_key = "openai_oauth_model" if provider == "openai-oauth" else "openai_model"
        for i, role in enumerate(roles):
            key = base_key if i == 0 else f"{base_key}_{role}"
            fallback = defaults[i] if i < len(defaults) else (
                "gpt-5.6-terra" if provider == "openai-oauth" else None)
            default_model = _load_last_openai_model(fallback, options, key, state_file)
            models.append(default_model if mode == "auto"
                          else _prompt_openai_model(default_model, model_info, label(role)))
            state_keys.append(key)
        os.environ["AI_WRITING_MODEL"] = models[0]
        os.environ["AI_REVIEW_MODEL"] = models[-1]
        os.environ["AI_OPENAI_MODEL"] = models[0]
    elif provider in CATALOGUE_PROVIDERS:
        for i, role in enumerate(roles):
            key = _model_state_key(provider) + ("" if i == 0 else f"_{role}")
            fallback = defaults[i] if i < len(defaults) else None
            default_model = _load_last_catalogue_model(provider, fallback, key, state_file)
            models.append(default_model if mode == "auto"
                          else _prompt_catalogue_model(provider, default_model, label(role)))
            state_keys.append(key)
        os.environ["AI_WRITING_MODEL"] = models[0]
        os.environ["AI_REVIEW_MODEL"] = models[-1]
        # The Claude Code CLI has no completion-token argument, so its catalogue
        # carries max_output 0 and the caps stay unset.
        write_out = _provider_models(provider)[models[0]][1]
        review_out = _provider_models(provider)[models[-1]][1]
        for key, max_out in (
            ("AI_WRITING_COMPLETION_TOKENS", write_out),
            ("AI_REVIEW_COMPLETION_TOKENS", review_out),
            ("AI_PLANNING_COMPLETION_TOKENS", write_out),
            ("AI_DEFAULT_COMPLETION_TOKENS", write_out),
        ):
            if max_out:
                os.environ[key] = str(max_out)
            else:
                os.environ.pop(key, None)
    else:
        # No model menu for these providers; still show what the config runs on.
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        writing = data.get("writing_model") or ""
        review = data.get("review_model") or writing
        for mid in dict.fromkeys(filter(None, (writing, review))):
            print(f"Model {mid}: {_facts_label(_model_facts(provider, mid))} ($ per 1M in/out)")
        models = [writing] + [review] * (len(roles) - 1)

    first = models[0] if state_keys else None
    _save_last_provider(
        provider,
        first if provider in ("openai", "openai-oauth") else None,
        first if provider in CATALOGUE_PROVIDERS else None,
        state_file,
        dict(zip(state_keys[1:], models[1:])),
    )
    return provider, config_path, models


def run(
    provider: str,
    mode: str = "review",
    fresh: bool = False,
    continuous: bool = False,
    publish_kdp: bool = False,
    kdp_visible: bool = False,
    *,
    ask_models: bool = False,
    resume: bool = False,
    forever: bool = False,
    pause: int = 0,
    retry_wait: int = 900,
    publish: str = "",
) -> bool:
    choose_ai(provider, "review" if ask_models else mode)
    publish = publish or ("kdp" if publish_kdp else "")

    if resume and not PROJECT_STATE_FILE.exists():
        raise ValueError(f"--resume: no saved project at {PROJECT_STATE_FILE}")
    if os.getenv("AI_BOOK_IDEA") and PROJECT_STATE_FILE.exists() and not fresh:
        raise ValueError("A saved project exists, so the idea would be ignored. Add --fresh to "
                         "archive its ebooks and start the idea, or drop the idea to resume it.")
    if resume:
        print("Resuming existing project.")
    elif fresh:
        if mode == "auto":
            _stash_previous_ebook_files()
            _clear_project_output()
        else:
            _prepare_fresh_start()
    elif PROJECT_STATE_FILE.exists():
        if mode == "auto":
            print("Resuming existing project.")
        elif _prompt_resume_existing_project():
            print("Resuming existing project.")
        else:
            _prepare_fresh_start()
    elif _has_previous_generated_artifacts():
        if mode == "auto":
            _stash_previous_ebook_files()
            _clear_project_output()
        else:
            _prepare_fresh_start()

    creator = None
    exit_on_ctrl_c(lambda: creator and creator.project_manager.save_project(),
                   "Process interrupted by user. Progress has been saved.")
    while True:
        creator = AIBookCreator()
        completed = creator.create_book()
        if completed and publish:
            _publish(creator, publish, kdp_visible)
        if not completed and forever:
            # Budget pauses, provider outages and broken steps all leave resumable
            # state; wait and pick the same project up again.
            # ponytail: retries the same project indefinitely; add a failure cap if one
            # book can wedge the loop for good.
            print(f"\nForever mode: book not finished; resuming in {retry_wait}s.")
            time.sleep(retry_wait)
            continue
        if not (continuous or forever) or not completed:
            return completed

        # The user's idea seeds the first project only; the AI invents the rest.
        os.environ.pop("AI_BOOK_IDEA", None)
        moved = _stash_previous_ebook_files()
        if moved:
            print(f"Archived completed KDP package to: {moved[0].parent}")
        _clear_project_output()
        if pause:
            time.sleep(pause)
        print("\nContinuous mode: starting the next project.")


def _publish(creator, target: str, kdp_visible: bool = False) -> str:
    """KDP first when asked; GitHub release when KDP fails or is not wanted."""
    package = creator.project_manager.get_step_data("publishing").get("package_file", "")
    if not package:
        print("Publishing skipped: no package was prepared.")
        return ""
    if target == "kdp":
        from .utils.kdp_publisher import publish_package

        try:
            publish_package(package, headless=not kdp_visible, ai_service=creator.ai_service)
            return "kdp"
        except Exception as exc:
            print(f"KDP publishing failed ({exc}); trying GitHub.")
    from .utils import github_publisher

    try:
        print(f"Published on GitHub: {github_publisher.publish_package(package)}")
        return "github"
    except Exception as exc:
        print(f"GitHub publishing skipped: {exc}")
        return ""


def _range_arg(value: str) -> str:
    if not re.fullmatch(r"\d+(?:-\d+)?", value.strip()):
        raise argparse.ArgumentTypeError("use a number or low-high range, such as 220-300")
    low, *rest = map(int, value.split("-"))
    if low < 1 or (rest and rest[0] < low):
        raise argparse.ArgumentTypeError("range must be positive and ordered low-to-high")
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and package an AI-assisted book.")
    parser.add_argument("idea", nargs="*",
                        help="book idea, anywhere on the line (quoted or not): the first project's "
                             "concept in any mode; later --forever projects are the AI's")
    parser.add_argument("--mode", choices=("review", "auto"), default=os.getenv("AI_BOOK_MODE", "review"))
    parser.add_argument("--provider", choices=tuple(PROVIDER_CONFIG_MAP))
    parser.add_argument("--author")
    parser.add_argument("--pages", type=_range_arg, metavar="MIN-MAX")
    parser.add_argument("--chapters", type=_range_arg, metavar="MIN-MAX")
    parser.add_argument("--series", type=int, metavar="BOOKS")
    parser.add_argument("--cover-source", choices=("manual", "perchance", "pollinations"))
    parser.add_argument("--cover-background", metavar="IMAGE")
    parser.add_argument(
        "--publish-kdp",
        action="store_true",
        help="submit the completed eBook to KDP using the saved Chrome profile",
    )
    parser.add_argument(
        "--kdp-visible",
        action="store_true",
        help="show Chrome during KDP publishing (headless is the default)",
    )
    parser.add_argument("--publish", action="store_true",
                        help="publish each finished book on KDP, falling back to a GitHub release")
    parser.add_argument("--publish-github", action="store_true",
                        help="publish each finished book as a GitHub release, skipping KDP")
    parser.add_argument("--fresh", action="store_true", help="archive old ebook assets and start a new project")
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="keep creating and packaging new books until interrupted or the provider stops",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="ask only for provider and models; the AI invents the idea and makes every choice",
    )
    parser.add_argument(
        "--forever",
        action="store_true",
        help="--auto, then start a new project after each finished book or series; "
             "unfinished work is resumed after --retry-wait",
    )
    parser.add_argument("--resume", action="store_true", help="resume the saved project without asking")
    parser.add_argument("--pause", type=int, default=0, help="seconds between projects in --forever")
    parser.add_argument("--retry-wait", type=int, default=900,
                        help="seconds before --forever resumes an unfinished book")
    # Intermixed: the idea's words may sit before, between or after the options.
    args = parser.parse_intermixed_args()
    args.idea = " ".join(args.idea).strip()
    args.publish = "github" if args.publish_github else "kdp" if args.publish else ""
    if args.forever:
        args.auto = True
    if args.auto:
        args.mode = "auto"
    if args.resume and args.fresh:
        parser.error("--resume and --fresh contradict each other")
    return args


def _existing_author() -> str:
    try:
        data = json.loads(PROJECT_STATE_FILE.read_text(encoding="utf-8"))
        return str(data.get("init", {}).get("author_name", "")).strip()
    except Exception:
        return ""


def main() -> None:
    """Run the AI Book Creator interactively."""
    print("AI Book Creator v2.0 - Modular Edition with Glossary Support")
    print("=" * 60)

    try:
        args = _parse_args()
        if args.continuous and args.mode != "auto":
            raise ValueError("--continuous requires --mode auto")
        if args.kdp_visible and not args.publish_kdp:
            raise ValueError("--kdp-visible requires --publish-kdp")
        os.environ["AI_BOOK_MODE"] = args.mode
        if args.idea:
            os.environ["AI_BOOK_IDEA"] = args.idea
        author = args.author or os.getenv("AI_BOOK_AUTHOR") or _existing_author()
        if not author and args.mode == "review":
            author = input("Author name [AI Book Creator]: ").strip()
        os.environ["AI_BOOK_AUTHOR"] = author or "AI Book Creator"
        os.environ["AI_MODELS_USED_PATH"] = str(PROJECT_OUTPUT_DIR / "models_used.json")
        if args.pages:
            os.environ["AI_BOOK_PAGE_RANGE"] = args.pages
        if args.chapters:
            os.environ["AI_BOOK_CHAPTER_RANGE"] = args.chapters
        if args.series is not None:
            if args.series < 1:
                raise ValueError("--series must be at least 1")
            os.environ["AI_BOOK_SERIES_COUNT"] = str(args.series)
        if args.cover_background:
            os.environ["AI_BOOK_COVER_BACKGROUND"] = args.cover_background
        os.environ["AI_BOOK_COVER_SOURCE"] = args.cover_source or "pollinations"

        default_provider = _load_last_provider()
        provider = args.provider or (default_provider if args.mode == "auto" and not args.auto
                                     else _prompt_provider(default_provider))
        run(
            provider,
            args.mode,
            args.fresh,
            args.continuous,
            args.publish_kdp,
            args.kdp_visible,
            ask_models=args.auto,
            resume=args.resume,
            forever=args.forever,
            pause=args.pause,
            retry_wait=args.retry_wait,
            publish=args.publish or "",
        )
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user. Progress has been saved.")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback

        traceback.print_exc()
        print("\nProgress has been saved. You can try to resume later.")


if __name__ == "__main__":
    main()
