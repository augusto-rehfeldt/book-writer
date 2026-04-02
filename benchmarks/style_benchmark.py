"""
Benchmark suite for chapter-opening variety and prompt anti-repetition rules.

Run from the repository root:
    python benchmarks/style_benchmark.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_book_creator.steps.step_1_structure import StructureStep
from ai_book_creator.steps.step_2_write import WriteStep
from ai_book_creator.utils.style_checks import (
    analyze_openings,
    format_benchmark_report,
    prompt_mentions,
)


SAMPLE_INIT_DATA = {
    "book_idea": (
        "An alternate 1950s space race where the USA, Nazi Germany, the USSR, and Imperial Japan "
        "compete to reach orbit and militarize the moon."
    ),
    "layout_content": (
        "A hard-science alternate history novel with engineers, intelligence officers, pilots, and "
        "political operators under pressure."
    ),
}


SAMPLE_OUTLINES = [
    {
        "chapter_number": 1,
        "title": "Four Powers, One Sky",
        "opening_style": "dialogue-led",
        "plot_outline": "Chapter one begins with a tense launch-room argument and a rumor of a foreign satellite.",
    },
    {
        "chapter_number": 2,
        "title": "The Rocket Men",
        "opening_style": "procedural action",
        "plot_outline": "The second chapter follows a propulsion fix in the desert as the engine finally steadies.",
    },
    {
        "chapter_number": 3,
        "title": "Ashes of the Old War",
        "opening_style": "quiet reflection",
        "plot_outline": "The third chapter turns inward and explains the postwar balance through memory and briefing.",
    },
    {
        "chapter_number": 4,
        "title": "First Orbit",
        "opening_style": "suspense hook",
        "plot_outline": "A launch succeeds just enough to trigger panic in every capital.",
    },
]


class _PromptStubAI:
    provider = "openai"

    def build_sectioned_prompt(
        self,
        instruction: str,
        sections: list[tuple[str, str]],
        max_prompt_tokens: int | None = None,
        section_token_caps: dict[str, int] | None = None,
        safety_margin: float | None = None,
    ) -> str:
        lines = [instruction.strip()]
        for heading, text in sections:
            lines.append(f"{heading}: {text.strip()}")
        return "\n\n".join(lines)


class _DummyProjectManager:
    def __init__(self, data: dict[str, Any]):
        self.data = data

    def get_step_data(self, name: str) -> dict[str, Any]:
        return self.data.get(name, {})


def _build_prompt_samples() -> tuple[str, str]:
    structure_step = StructureStep(_PromptStubAI(), _DummyProjectManager({"init": SAMPLE_INIT_DATA}), None)
    structure_prompt, _ = structure_step._build_structure_prompt(SAMPLE_INIT_DATA)

    write_step = WriteStep(
        _PromptStubAI(),
        _DummyProjectManager({"init": SAMPLE_INIT_DATA, "structure": {"chapter_plots": {}}}),
        None,
        str(ROOT / "book_output"),
    )
    chapter_prompt = write_step._build_chapter_prompt(
        {
            "title": SAMPLE_OUTLINES[0]["title"],
            "plot_outline": SAMPLE_OUTLINES[0]["plot_outline"],
            "opening_style": SAMPLE_OUTLINES[0]["opening_style"],
        },
        "",
        1000,
    )
    return structure_prompt, chapter_prompt


def _load_real_chapters() -> list[str]:
    output_dir = ROOT / "book_output"
    files = sorted(output_dir.glob("chapter_*.txt"))
    return [path.read_text(encoding="utf-8") for path in files if path.is_file()]


def _simulate_corpus(strict: bool) -> list[str]:
    opener_map = {
        "dialogue-led": '"Again," Evelyn said, and the room fell quiet.',
        "procedural action": "Mercer reached for the relay, reset the feed, and watched the line settle.",
        "quiet reflection": "For one beat, Evelyn let the noise around her fall away.",
        "suspense hook": "The phone rang once, and nobody in the room moved.",
    }
    fallback_openers = [
        "Again, Evelyn said.",
        "Again, the numbers were wrong.",
        "The room hummed around her.",
        "The air in the hangar felt heavy.",
    ]

    simulated_texts: list[str] = []
    for index, outline in enumerate(SAMPLE_OUTLINES):
        if strict:
            opener = opener_map.get(outline["opening_style"], fallback_openers[index % len(fallback_openers)])
        else:
            opener = fallback_openers[index % len(fallback_openers)]

        simulated_texts.append(
            f"# Chapter {outline['chapter_number']}: {outline['title']}\n\n"
            f"{opener} The rest of the scene unfolds with moderate conflict and a generic transition."
        )

    return simulated_texts


def run_style_benchmark() -> dict[str, Any]:
    structure_prompt, chapter_prompt = _build_prompt_samples()
    prompt_checks = {
        "structure_mentions_opening_style": prompt_mentions(
            structure_prompt,
            ["Opening style tag", "vary the opening style tag", "specific, concrete, and distinct"],
        ),
        "write_mentions_antirepetition": prompt_mentions(
            chapter_prompt,
            ["Do not reuse the same first-sentence structure", "Do not begin with \"Again\"", "template openings"],
        ),
    }

    baseline_corpus = _load_real_chapters() or _simulate_corpus(strict=False)
    candidate_corpus = _simulate_corpus(strict=True)

    baseline_result = analyze_openings(baseline_corpus)
    candidate_result = analyze_openings(candidate_corpus)

    improved = (
        candidate_result.passes
        and candidate_result.diversity_score > baseline_result.diversity_score
        and candidate_result.duplicate_openers <= baseline_result.duplicate_openers
        and candidate_result.banned_phrase_hits <= baseline_result.banned_phrase_hits
    )

    return {
        "prompt_checks": prompt_checks,
        "baseline": baseline_result,
        "candidate": candidate_result,
        "improved": improved,
    }


def main() -> int:
    report = run_style_benchmark()

    print("STYLE BENCHMARK")
    print("=" * 60)
    print(
        "Prompt checks: "
        f"structure={report['prompt_checks']['structure_mentions_opening_style']}, "
        f"write={report['prompt_checks']['write_mentions_antirepetition']}"
    )
    print(format_benchmark_report("Baseline", report["baseline"]))
    print(format_benchmark_report("Candidate", report["candidate"]))
    print(f"Improved: {report['improved']}")

    success = all(report["prompt_checks"].values()) and report["improved"]
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
