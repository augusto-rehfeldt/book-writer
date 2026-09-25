"""Passage edits, content-preservation checks, and manuscript continuity."""

import re

from .text_utils import ask_json, parse_json, text_chunks, text_digest


EDITORIAL_CRITERIA = (
    "Causal continuity; believable motivation and emotional change; distinct character "
    "voices; concrete, viewpoint-specific detail; useful subtext; clear spatial action; "
    "pacing appropriate to the scene; no redundant explanation. Preserve deliberate "
    "ambiguity, idiom, rough edges, refrains and quiet narration. A statistical style "
    "outlier alone is not a defect. Do not add dialogue, adverbs or sentence shapes "
    "to meet a quota. Do not estimate who wrote the prose."
)


def story_context(init_data: dict, memory: str = "", glossary: str = "") -> str:
    return (
        f"BOOK CONCEPT:\n{init_data.get('book_idea', '')}\n\n"
        f"BOOK PLAN AND VOICE (intentions, not events already experienced):\n"
        f"{init_data.get('layout_content', '')}\n\n"
        f"CANONICAL IDENTITIES (planned descriptions may contain future events):\n{glossary}\n\n"
        f"STORY SO FAR, FROM FINISHED PROSE:\n{memory or 'The book has not begun.'}\n\n"
        "Finished prose takes precedence over the plan. Do not give characters knowledge "
        "of future events. Preserve viewpoint, tense, narrative distance and speech habits."
    )


def _continuity_problem(result: dict):
    value = result.get("continuity")
    if not isinstance(value, str) or not value.strip():
        return 'expected a non-empty "continuity" string'
    if len(value.split()) > 900:
        return (f"the record ran to {len(value.split())} words; condense it to at most "
                "600 words, merging settled facts and keeping unresolved ones")
    return None


def update_continuity(ai_service, text: str, previous: str = "", title: str = "") -> str:
    """Read every passage, carrying forward a compact factual record."""
    memory = previous
    passages = list(text_chunks(text))
    for index, passage in enumerate(passages, 1):
        print(f"    continuity passage {index}/{len(passages)}...")
        prompt = (
            "Update a continuity record from this manuscript passage. Return JSON only: "
            '{"continuity": "record, at most 600 words"}. Preserve unresolved earlier '
            "facts. Track chronology, locations, injuries, possessions, relationships, "
            "who knows each secret, emotional changes, promises, unresolved threads and "
            "the current ending. Include chapter references and narrator/character speech "
            "habits. Record only what the prose establishes; distinguish belief from fact. "
            "Keep the record factual, with no critique or invented explanations.\n\n"
            f"PREVIOUS RECORD:\n{memory}\n\nCHAPTER: {title}, passage {index}\n{passage}"
        )
        memory = ask_json(
            ai_service, prompt, _continuity_problem,
            f"Invalid continuity record for {title}; original manuscript retained",
            model_type="review", max_completion_tokens=2048)["continuity"].strip()
    return memory


def apply_edits(text: str, edits: list, max_ratio: float = 1.15) -> str:
    """Exact, nonoverlapping replacements; never regenerate unselected prose."""
    if not isinstance(edits, list):
        raise ValueError("edits must be a list")
    spans = []
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("edit must be an object")
        original, replacement = edit.get("original"), edit.get("replacement")
        if not isinstance(original, str) or not original.strip() or not isinstance(replacement, str):
            raise ValueError("edit must contain an exact original and a replacement")
        start = text.find(original)
        if start < 0 or text.find(original, start + 1) >= 0:
            raise ValueError("edit quote is missing or ambiguous")
        if not isinstance(edit.get("reason"), str) or not edit["reason"].strip():
            raise ValueError("edit needs an editorial reason")
        spans.append((start, start + len(original), replacement))
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError("overlapping edits")
    candidate = text
    for start, end, replacement in reversed(spans):
        candidate = candidate[:start] + replacement + candidate[end:]
    markers = lambda value: re.findall(r"(?m)^\s*(?:#+[^\n]*|\*\*\*|---)\s*$", value)
    if markers(candidate) != markers(text):
        raise ValueError("chapter headings or scene breaks changed")
    words = max(1, len(text.split()))
    if not 0.9 * words <= len(candidate.split()) <= max_ratio * words:
        raise ValueError("revision changes passage length beyond its allowance")
    return candidate


def prefer_revision(ai_service, original: str, candidate: str, context: str = "") -> dict:
    """Require a clear editorial win and preservation in both presentation orders."""
    config = getattr(ai_service, "config", {}) or {}
    writing_model = getattr(ai_service, "writing_model", config.get("writing_model"))
    judges = [m for m in (config.get("judge_models") or []) if m != writing_model]
    kwargs = {"model": judges[0]} if judges else {}
    verdicts = []
    for swapped in (False, True):
        a, b = (candidate, original) if swapped else (original, candidate)
        prompt = (
            "Compare two versions of the same passage without guessing their authors. "
            f"Criteria: {EDITORIAL_CRITERIA}\n"
            "A tie is appropriate when neither clearly improves the scene. Check both "
            "versions against the context. Return JSON only: "
            '{"winner":"A|B|tie", "same_events":true, "same_character_intent":true, '
            '"no_new_facts":true, "reason":"specific evidence for the preference"}. '
            "Set preservation flags false when an event, revelation, dialogue meaning, "
            "motivation, timeline fact or uncertainty has been lost or changed. A repair "
            "of a demonstrated contradiction counts as preserved only when the supplied "
            "context explicitly supports it. Never infer new facts to justify a repair.\n\n"
            f"CONTEXT:\n{context}\n\nVERSION A:\n{a}\n\nVERSION B:\n{b}"
        )
        result = parse_json(ai_service.generate_content(
            prompt, model_type="review", max_completion_tokens=1024, **kwargs)) or {}
        expected = "A" if swapped else "B"
        accepted = (
            result.get("winner") == expected
            and all(result.get(key) is True for key in
                    ("same_events", "same_character_intent", "no_new_facts"))
            and isinstance(result.get("reason"), str) and bool(result["reason"].strip())
        )
        verdicts.append({"candidate_label": expected, "verdict": result})
        if not accepted:
            return {"accepted": False, "comparisons": verdicts}
    return {"accepted": True, "comparisons": verdicts}


def edit_text(ai_service, text: str, instruction: str, context: str = "", *,
              max_ratio: float = 1.15, diagnose=None) -> tuple[str, list]:
    """Inspect every passage, apply verified edits, and retain quoted findings."""
    passages = list(text_chunks(text))
    output, reports = [], []
    for index, passage in enumerate(passages):
        hints = diagnose(passage) if diagnose else instruction
        if diagnose and not hints:
            output.append(passage)
            continue
        neighbors = (
            f"{context}\n\nPRECEDING PROSE (context only):\n"
            f"{''.join(output)[-1800:]}\n\nFOLLOWING PROSE (context only):\n"
            f"{passages[index + 1][:1800] if index + 1 < len(passages) else '[chapter ends]'}"
        )
        prompt = (
            "Inspect this passage as a fiction editor. Propose only necessary local edits; "
            "leave strong passages intact. Return JSON only: "
            '{"summary":"events in this passage, at most 150 words", '
            '"issues":[{"quote":"exact passage quote", "problem":"specific issue", '
            '"fix":"proposed repair"}], "edits":[{"original":"exact unique quote", '
            '"replacement":"replacement prose", "reason":"specific improvement"}]}. '
            "Use empty lists if no repair is needed. Keep every event, dialogue meaning, "
            "character intention, ambiguity, chapter heading and scene break. Do not change "
            "the prose outside your quoted edits. Do not edit the surrounding context. "
            f"Keep passage length between 90% and {max_ratio:.0%} of its original.\n"
            f"CRITERIA: {EDITORIAL_CRITERIA}\nTASK: {instruction}\n"
            f"DIAGNOSTIC HINTS (verify against this passage): {hints}\n\n"
            f"{neighbors}\n\nPASSAGE TO EDIT:\n{passage}"
        )
        result = ask_json(
            ai_service, prompt,
            lambda r: None if (isinstance(r.get("summary"), str) and isinstance(r.get("issues"), list)
                               and isinstance(r.get("edits"), list))
            else '"summary" must be a string and "issues" and "edits" must be lists',
            "Incomplete editorial response; manuscript retained for retry",
            model_type="review", max_completion_tokens=4096)
        report = {"passage": index + 1, "source_hash": text_digest(passage),
                  "summary": result["summary"], "issues": result["issues"],
                  "edits": result["edits"], "accepted": False}
        candidate = passage
        if result["edits"]:
            try:
                proposed = apply_edits(passage, result["edits"], max_ratio)
            except ValueError as exc:
                report["rejection"] = str(exc)
            else:
                if proposed != passage:
                    report.update(prefer_revision(ai_service, passage, proposed, neighbors))
                    if report["accepted"]:
                        candidate = proposed
        reports.append(report)
        output.append(candidate)
    return "".join(output), reports
