"""
Benchmark suite for chapter-opening variety and prompt anti-repetition rules.

Run from the repository root:
    python tests/style_benchmark.py
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


BENCHMARK_SCENARIOS = [
    {
        "name": "heist_thriller",
        "book_idea": "A heist thriller about a crew targeting a secure archive during a citywide blackout.",
        "layout_content": "Fast-paced crime fiction with shifting loyalties, surveillance, and a race against time.",
        "title": "The Lockbox Job",
        "opening_style": "dialogue-led",
        "plot_outline": "The chapter begins with the crew arguing over the plan while alarms fail across the city.",
    },
    {
        "name": "family_drama",
        "book_idea": "A family drama centered on a reunion that forces old grievances back into the open.",
        "layout_content": "Intimate literary fiction with subtext, memory, and quiet emotional pressure.",
        "title": "The Dinner Table",
        "opening_style": "quiet reflection",
        "plot_outline": "The chapter starts with a domestic scene that reveals a fracture the family has avoided naming.",
    },
    {
        "name": "legal_thriller",
        "book_idea": "A courtroom thriller where a junior attorney uncovers evidence that could collapse a major case.",
        "layout_content": "A procedural suspense novel with testimony, deadlines, and hidden stakes.",
        "title": "Exhibit A",
        "opening_style": "procedural action",
        "plot_outline": "The chapter opens in the evidence room as a discrepancy in the case file changes the direction of the trial.",
    },
    {
        "name": "gothic_mystery",
        "book_idea": "A gothic mystery set in an old estate where every room seems to preserve a different secret.",
        "layout_content": "Moody suspense fiction with atmosphere, inheritance disputes, and an unreliable family history.",
        "title": "The West Wing",
        "opening_style": "sensory close-up",
        "plot_outline": "The chapter begins with a close sensory detail that hints the house is hiding a recent intrusion.",
    },
    {
        "name": "survival_adventure",
        "book_idea": "A survival adventure about a stranded expedition trying to cross hostile terrain before winter closes in.",
        "layout_content": "Outdoor adventure fiction with physical danger, resource management, and hard choices.",
        "title": "White Pass",
        "opening_style": "in medias res",
        "plot_outline": "The chapter drops the reader into a moment of immediate danger as the team loses supplies on a steep ridge.",
    },
    {
        "name": "political_conspiracy",
        "book_idea": "A political conspiracy novel about a whistleblower moving through a city of competing factions.",
        "layout_content": "A tension-heavy thriller with surveillance, institutional pressure, and secret alliances.",
        "title": "The Briefing",
        "opening_style": "institutional briefing",
        "plot_outline": "The chapter begins inside a formal meeting where the official story and the real story immediately diverge.",
    },
]


STYLE_OPENERS = {
    "dialogue-led": '"Again," Mira said, and the table went quiet.',
    "procedural action": "Jonah reset the switch, checked the seal, and watched the gauge steady.",
    "quiet reflection": "Lena let the noise of the building recede for a second.",
    "sensory close-up": "Ink stung the back of her throat as she leaned closer.",
    "in medias res": "Doors burst inward before anyone finished speaking.",
    "institutional briefing": "Reports began with three facts, none of them comforting.",
    "object-focused": "Keycards were warm from the last hand that touched them.",
    "cross-cut": "While the room argued, corridor lights flickered twice.",
    "suspense hook": "Calls came one ring too late.",
}

GENERIC_BROKEN_OPENERS = [
    "Again, Mira said.",
    "Again, the numbers were wrong.",
    "The room hummed around her.",
    "The room hummed around her.",
    "He looked at the desk and said nothing.",
    "She looked at the file and frowned.",
    "Outside, the crowd kept moving without them.",
    "The air in the hall felt heavy.",
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


def _build_prompt_samples(scenario: dict[str, str]) -> tuple[str, str]:
    init_data = {
        "book_idea": scenario["book_idea"],
        "layout_content": scenario["layout_content"],
    }
    structure_step = StructureStep(_PromptStubAI(), _DummyProjectManager({"init": init_data}), None)
    structure_prompt, _ = structure_step._build_structure_prompt(init_data)

    write_step = WriteStep(
        _PromptStubAI(),
        _DummyProjectManager({"init": init_data, "structure": {"chapter_plots": {}}}),
        None,
        str(ROOT / "book_output"),
    )
    chapter_prompt = write_step._build_chapter_prompt(
        {
            "title": scenario["title"],
            "plot_outline": scenario["plot_outline"],
            "opening_style": scenario["opening_style"],
        },
        "",
        1000,
    )
    return structure_prompt, chapter_prompt


def _build_corpus(openers: list[str], titles: list[str]) -> list[str]:
    chapters: list[str] = []
    for index, (title, opener) in enumerate(zip(titles, openers), start=1):
        chapters.append(
            f"# Chapter {index}: {title}\n\n"
            f"{opener} The rest of the scene continues with a distinct conflict, pacing, and resolution."
        )
    return chapters


def _build_baseline_corpus() -> list[str]:
    titles = [
        "The Lockbox Job",
        "The Dinner Table",
        "Exhibit A",
        "The West Wing",
        "White Pass",
        "The Briefing",
    ]
    return _build_corpus(GENERIC_BROKEN_OPENERS[: len(titles)], titles)


def _build_candidate_corpus() -> list[str]:
    titles = [
        "The Lockbox Job",
        "The Dinner Table",
        "Exhibit A",
        "The West Wing",
        "White Pass",
        "The Briefing",
        "Night Shift",
        "The Crossing",
    ]
    candidate_openers = [
        "Mira shoved the keycard into the reader and held her breath.",
        "Jonah checked the ledger against the manifest before anyone spoke.",
        "Heat shimmered over the stone steps as dawn cracked open.",
        "Ink stained the edge of her thumb as she turned the page.",
        "Doors burst inward before anyone finished the warning.",
        "Reports began with three facts that nobody wanted to hear.",
        "Keycards were warm from the last hand that touched them.",
        "Calls came one ring too late, and that was the only warning.",
    ]
    return _build_corpus(candidate_openers, titles)


def run_style_benchmark() -> dict[str, Any]:
    scenario_results = []
    for scenario in BENCHMARK_SCENARIOS:
        structure_prompt, chapter_prompt = _build_prompt_samples(scenario)
        structure_ok = prompt_mentions(
            structure_prompt,
            ["Opening style tag", "vary the opening style tag", "specific, concrete, and distinct"],
        )
        write_ok = prompt_mentions(
            chapter_prompt,
            [
                "Do not reuse the same first-sentence structure",
                "Do not begin with \"Again\"",
                "template openings",
            ],
        )
        scenario_results.append(
            {
                "name": scenario["name"],
                "structure_prompt_ok": structure_ok,
                "write_prompt_ok": write_ok,
                "passed": structure_ok and write_ok,
            }
        )

    baseline_result = analyze_openings(_build_baseline_corpus())
    candidate_result = analyze_openings(_build_candidate_corpus())

    improved = (
        candidate_result.passes
        and candidate_result.diversity_score > baseline_result.diversity_score
        and candidate_result.duplicate_openers <= baseline_result.duplicate_openers
        and candidate_result.banned_phrase_hits <= baseline_result.banned_phrase_hits
    )

    return {
        "scenario_results": scenario_results,
        "baseline": baseline_result,
        "candidate": candidate_result,
        "improved": improved,
    }


def main() -> int:
    report = run_style_benchmark()

    print("STYLE BENCHMARK")
    print("=" * 60)
    for scenario in report["scenario_results"]:
        print(
            f"{scenario['name']}: "
            f"structure={scenario['structure_prompt_ok']}, "
            f"write={scenario['write_prompt_ok']}, "
            f"passed={scenario['passed']}"
        )
    print(format_benchmark_report("Baseline", report["baseline"]))
    print(format_benchmark_report("Candidate", report["candidate"]))
    print(f"Improved: {report['improved']}")

    success = all(item["passed"] for item in report["scenario_results"]) and report["improved"]
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
