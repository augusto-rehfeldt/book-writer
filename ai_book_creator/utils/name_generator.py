"""
Name pool generator – creates large lists of plausible names per category
to avoid LLM‑generated repetitive or nonsensical names.
"""

from __future__ import annotations

import json
import random
import re
from typing import Dict, List, Optional, Any

from ..services.ai_service import AIService


def generate_name_pools(
    book_idea: str,
    layout_content: str,
    series_layout: str,
    ai_service: AIService,
    pool_size: int = 30,
) -> Dict[str, List[str]]:
    """
    Analyse the book context, identify relevant name categories, and generate
    a pool of plausible names for each category.

    Args:
        book_idea: The user's book concept.
        layout_content: Detailed layout (characters, settings, themes).
        series_layout: Optional series‑wide context.
        ai_service: AI service to call for generation.
        pool_size: How many names to generate per category (10‑100 recommended).

    Returns:
        Dictionary mapping category names to lists of names.
    """
    # Step 1: identify categories
    categories = _identify_name_categories(book_idea, layout_content, series_layout, ai_service)
    if not categories:
        # fallback – generic categories
        categories = ["protagonists", "antagonists", "supporting_characters", "locations", "concepts"]

    pools: Dict[str, List[str]] = {}
    for cat in categories:
        names = _generate_names_for_category(cat, book_idea, layout_content, series_layout, ai_service, pool_size)
        if names:
            pools[cat] = names
        else:
            pools[cat] = []

    # also generate a generic "any_character" pool as a safety net
    if "any_character" not in pools:
        generic = _generate_names_for_category(
            "any_character", book_idea, layout_content, series_layout, ai_service, pool_size
        )
        if generic:
            pools["any_character"] = generic

    return pools


def _identify_name_categories(
    book_idea: str, layout_content: str, series_layout: str, ai_service: AIService
) -> List[str]:
    """Ask the AI to list relevant name categories (cultures, roles, nationalities, etc.)."""
    prompt = f"""Analyse the following book concept and layout. List the distinct categories for which we need original names.

Categories can be:
- Roles: protagonist, antagonist, mentor, sidekick, etc.
- Cultures / nationalities: e.g., "Valyrian", "Nordic", "Martian", "French"
- Species / races: e.g., "Elves", "Dwarves", "AI entities"
- Organisations: e.g., "The Council", "Rebel faction"
- Locations: e.g., "Cities", "Mountains", "Planets"
- Concepts / magic systems: e.g., "Spells", "Artifacts"

Return ONLY a JSON list of strings, e.g. ["protagonist", "antagonist", "Nordic", "Elves", "Cities"].

Book idea: {book_idea[:500]}

Layout: {layout_content[:1500]}

Series layout: {series_layout[:800] if series_layout else "None"}
"""
    try:
        response = ai_service.generate_content(prompt, max_completion_tokens=400)
        # extract JSON list
        match = re.search(r"\[.*\]", response, re.DOTALL)
        if match:
            categories = json.loads(match.group())
            if isinstance(categories, list):
                # clean and deduplicate
                return list({str(c).strip().lower() for c in categories if str(c).strip()})
    except Exception:
        pass
    return []


def _generate_names_for_category(
    category: str,
    book_idea: str,
    layout_content: str,
    series_layout: str,
    ai_service: AIService,
    count: int,
) -> List[str]:
    """Generate `count` plausible names for a single category."""
    if count <= 0:
        return []
    count = min(count, 100)  # cap at 100

    prompt = f"""Generate {count} original, culturally appropriate, and varied names for the category: "{category}".

Context:
Book idea: {book_idea[:400]}
Layout: {layout_content[:1000]}
Series: {series_layout[:500] if series_layout else "None"}

Rules:
- Names must fit the genre and tone.
- Avoid real‑world celebrity names or obvious trademarks.
- Mix lengths, syllable counts, and letter patterns.
- If the category is a role (e.g., "protagonist"), generate first names suitable for that role.
- If the category is a culture/nationality, generate full names (first + last) or clan names.

Return ONLY a JSON list of strings, e.g. ["Aelar", "Brynn", "Caelum", ...]. No extra text.
"""
    try:
        response = ai_service.generate_content(prompt, max_completion_tokens=2000)
        match = re.search(r"\[.*\]", response, re.DOTALL)
        if match:
            names = json.loads(match.group())
            if isinstance(names, list):
                # clean and deduplicate, limit to count
                cleaned = []
                seen = set()
                for n in names:
                    n_str = str(n).strip()
                    if n_str and n_str not in seen:
                        seen.add(n_str)
                        cleaned.append(n_str)
                return cleaned[:count]
    except Exception:
        pass
    return []


def pick_random_name(pools: Dict[str, List[str]], category: str, fallback_category: str = "any_character") -> Optional[str]:
    """Pick a random name from a specific category pool, or from the fallback pool."""
    pool = pools.get(category)
    if pool:
        return random.choice(pool) if pool else None
    fallback = pools.get(fallback_category)
    if fallback:
        return random.choice(fallback)
    return None