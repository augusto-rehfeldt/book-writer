"""Passage edits, content-preservation checks, and manuscript continuity."""

import re

from .text_utils import ask_json, detail, parse_json, text_chunks, text_digest


EDITORIAL_CRITERIA = (
    "Causal continuity; believable motivation and emotional change; distinct character "
    "voices; concrete, viewpoint-specific detail; useful subtext; clear spatial action; "
    "pacing appropriate to the scene; no redundant explanation. Named, world-specific "
    "things over generic nouns; humor and opinion where the character has them. Preserve "
    "deliberate ambiguity, idiom, rough edges, digressions, refrains and quiet narration. "
    "A statistical style outlier alone is not a defect. Do not add dialogue, adverbs or "
    "sentence shapes to meet a quota. Do not estimate who wrote the prose."
)

# Professional editors' recurring fixes to LLM fiction (Chakrabarty et al., CHI 2025,
# LAMP) plus the narrative habits StoryScope (2026) found separate AI fiction.
REVISION_CHECKS = (
    "Look for: clichés and stock phrases; exposition that restates what a scene already "
    "showed, including a closing line that explains its meaning; aphoristic kicker lines "
    "that end a paragraph on a neat maxim; purple or overwrought phrasing; awkward word "
    "choice; generic unnamed things ('the vessel', 'the food') where the world has a "
    "name for them; recurring people known only by role ('the clerk', 'the official') "
    "where someone would know their name; narration built on what a character did not "
    "do or say ('She did not ask. He did not answer.') instead of what happened; "
    "dialogue lines that any character could have spoken, judged against "
    "the voice cards; everyone speaking in the same clipped register."
)


def voice_bible(ai_service, init_data: dict) -> str:
    """Character voice cards and world texture, derived once from the approved plan.

    Research on LLM fiction finds characters that sound alike and worlds without
    proper nouns; this gives the writer concrete, book-specific material to draw on
    instead of quotas.
    """
    return ai_service.generate_content(
        "From this approved book plan, write a voice bible for the novelist. Keep every "
        "name exactly as given; add no plot events.\n\n"
        "VOICE CARDS: for each main character, a short card with: vocabulary their work, "
        "place and upbringing give them (the metaphors they reach for); verbal tics and "
        "pet phrases; how much they talk and how that changes under stress (some talk more, "
        "circle, over-explain; not everyone goes terse); how they dodge a question; "
        "what they would never say aloud; swearing and formality; what they find funny and "
        "how they joke; one story or grievance they keep coming back to; three sample lines "
        "of their speech in different moods.\n\n"
        "WORLD TEXTURE: named things the characters live among, fitting the setting and "
        "period: foods and drinks, brands or makers, money and prices, songs, games, "
        "sayings and proverbs, curses, local institutions and nicknames, a running local "
        "joke, and the everyday objects the plot will need, each with its name in this "
        "world. For a real historical setting use only things that existed then.\n\n"
        "Plain text, compact, no commentary.\n\n"
        f"BOOK CONCEPT:\n{init_data.get('book_idea', '')}\n\n"
        f"BOOK PLAN:\n{init_data.get('layout_content', '')}",
        model_type="writing", max_completion_tokens=4000).strip()


_DIALOGUE = re.compile(r"[“\"]([^”\"\n]{15,400})[”\"]")


def _speakers(result: dict):
    return None if isinstance(result.get("speakers"), dict) else 'expected {"speakers": {...}}'


def indistinct_lines(ai_service, text: str, bible: str, limit: int = 60) -> list:
    """Dialogue a reader could not attribute from its words and the voice cards alone.

    One call names each line's speaker with the chapter in view; a second guesses
    from the line and the voice cards only. Disagreement means the line has no voice
    of its own. Research finds LLM characters sound alike; this measures it per book.
    """
    lines = _DIALOGUE.findall(text)[:limit]
    if len(lines) < 6 or not bible.strip():
        return []
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
    reply = 'Return JSON only: {"speakers": {"1": "Name", ...}}; use "unknown" when unsure.'
    truth = ask_json(ai_service, f"Who speaks each numbered line in this chapter? {reply}\n\n"
                     f"CHAPTER:\n{text}\n\nLINES:\n{numbered}", _speakers,
                     "Invalid speaker list", model_type="review", max_completion_tokens=2048)["speakers"]
    guess = ask_json(ai_service, "Guess who says each numbered line from its wording alone, using "
                     f"only these voice cards; you have no other context. {reply}\n\n"
                     f"VOICE CARDS:\n{bible}\n\nLINES:\n{numbered}", _speakers,
                     "Invalid speaker guesses", model_type="review", max_completion_tokens=2048)["speakers"]
    first = lambda name: str(name or "").strip().lower().split(" ")[0]
    return [lines[int(k) - 1] for k, who in truth.items()
            if str(k).isdigit() and 0 < int(k) <= len(lines) and first(who) not in ("", "unknown")
            and first(guess.get(k)) != first(who)]


def story_context(init_data: dict, memory: str = "", glossary: str = "") -> str:
    bible = init_data.get("voice_bible", "")
    return (
        f"BOOK CONCEPT:\n{init_data.get('book_idea', '')}\n\n"
        f"BOOK PLAN AND VOICE (intentions, not events already experienced):\n"
        f"{init_data.get('layout_content', '')}\n\n"
        + (f"VOICE CARDS AND WORLD TEXTURE (draw on these; not events):\n{bible}\n\n"
           if bible else "") +
        f"CANONICAL IDENTITIES (planned descriptions may contain future events):\n{glossary}\n\n"
        f"STORY SO FAR, FROM FINISHED PROSE:\n{memory or 'The book has not begun.'}\n\n"
        "Finished prose takes precedence over the plan. Do not give characters knowledge "
        "of future events. Preserve viewpoint, tense, narrative distance and speech habits."
    )


def _continuity_problem(result: dict):
    value = result.get("continuity")
    if not isinstance(value, str) or not value.strip():
        return 'expected a non-empty "continuity" string'
    return None


def _ask_record(ai_service, prompt: str, title: str) -> str:
    return ask_json(
        ai_service, prompt, _continuity_problem,
        f"Invalid continuity record for {title}; original manuscript retained",
        model_type="review", max_completion_tokens=2048)["continuity"].strip()


def _condense(ai_service, memory: str, title: str) -> str:
    """Shrink an oversized record by editing it, not by re-reading the passage.

    Re-asking the extraction prompt regenerates a record of the same length;
    handing the model its own record to condense converges.
    """
    shortest = memory
    for _ in range(3):
        words = len(memory.split())
        if words <= 900:
            return memory
        memory = _ask_record(ai_service, (
            f"This continuity record ran to {words} words. Condense it to at most 600 "
            "words: merge settled facts, drop resolved threads, and keep every unresolved "
            "fact, secret, promise, injury, possession and the current ending. Add nothing. "
            'Return JSON only: {"continuity": "condensed record"}.\n\n'
            f"CHAPTER: {title}\nRECORD:\n{memory}"), title)
        shortest = min(shortest, memory, key=lambda value: len(value.split()))
    # The record is derived notes, not manuscript: a weak model that cannot get
    # under the target keeps its shortest attempt rather than halting the book.
    # The 2048-token reply cap bounds how large it can grow.
    if len(shortest.split()) > 900:
        print(f"  Continuity record for {title} kept at {len(shortest.split())} words "
              "(model could not condense further)")
    return shortest


def update_continuity(ai_service, text: str, previous: str = "", title: str = "") -> str:
    """Read every passage, carrying forward a compact factual record."""
    memory = _condense(ai_service, previous, title) if previous else previous
    passages = list(text_chunks(text))
    for index, passage in enumerate(passages, 1):
        detail(f"    continuity passage {index}/{len(passages)}...")
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
        memory = _condense(ai_service, _ask_record(ai_service, prompt, title), title)
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
