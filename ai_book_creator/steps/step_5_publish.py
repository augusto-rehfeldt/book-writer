"""Step 5: Prepare a compliant Kindle Direct Publishing handoff package."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .base_step import BaseStep
from ..utils.cover_creator import create_cover
from ..utils.perchance_creator import generate_perchance_background


KDP_URL = "https://kdp.amazon.com/bookshelf"
PERCHANCE_URL = "https://perchance.org/ai-text-to-image-generator"


class PublishStep(BaseStep):
    def __init__(self, ai_service, project_manager, output_dir):
        super().__init__(ai_service, project_manager)
        self.step_name = "publishing"
        self.output_dir = Path(output_dir)

    def should_execute(self) -> bool:
        existing = self.get_step_data()
        package_file = Path(existing.get("package_file", ""))
        cover_file = existing.get("cover_file", "")
        ebook_file = self.project_manager.get_step_data("ebook").get("output_file", "")
        source_file = existing.get("source_ebook_file", "")
        automatic_cover = os.getenv("AI_BOOK_COVER_SOURCE", "pollinations").strip().lower() != "manual"
        return (
            not self.is_completed()
            or not package_file.is_file()
            or automatic_cover and not (cover_file and Path(cover_file).is_file())
            or not source_file
            or not ebook_file
            or Path(source_file).resolve() != Path(ebook_file).resolve()
        )

    def get_step_header(self) -> str:
        return "=" * 60 + "\n🚀 STEP 5: KDP PUBLISHING HANDOFF\n" + "=" * 60

    def execute(self) -> Dict[str, Any]:
        init_data = self.project_manager.get_step_data("init")
        ebook_data = self.project_manager.get_step_data("ebook")
        ebook_file = Path(ebook_data["output_file"]).resolve()
        prompt_file = Path(ebook_data["prompt_file"]).resolve()
        title = str(init_data.get("book_title") or ebook_file.stem.replace("-", " ").title())
        author = str(init_data.get("author_name") or "AI Book Creator")
        prompt = prompt_file.read_text(encoding="utf-8")

        cover_file = self._make_cover(ebook_file, prompt, title, author)
        suggestions = self._metadata_suggestions(init_data, ebook_data)
        package_file = ebook_file.with_name(f"{ebook_file.stem}_kdp.json")
        checklist_file = ebook_file.with_name(f"{ebook_file.stem}_KDP_CHECKLIST.txt")
        package = {
            "status": "ready_for_kdp_upload" if cover_file else "needs_cover",
            "kdp_url": KDP_URL,
            "language": "English",
            "title": title,
            "author": author,
            "description": str(ebook_data.get("description", ""))[:4000],
            "keywords": suggestions["keywords"],
            "category_hints": suggestions["category_hints"],
            "manuscript_file": str(ebook_file),
            "cover_file": cover_file,
            "suggested_list_price_usd": os.getenv("AI_BOOK_PRICE_USD", "0.99"),
            "ai_disclosure": {
                "text": "AI-generated",
                "images": "AI-generated" if cover_file and self._cover_is_ai() else "None",
                "translations": "None",
            },
            "ai_tools": {
                "text": os.getenv("AI_BOOK_TEXT_AI_TOOL") or self._text_ai_tool(),
                "images": os.getenv("AI_BOOK_IMAGE_AI_TOOL") or self._image_ai_tool(),
            },
            "publishing_rights": "I own the copyright and hold the necessary publishing rights.",
            "kdp_defaults": {
                "territories": "worldwide",
                "royalty": "35_PERCENT",
                "kdp_select": False,
                "drm": True,
                "headless": True,
            },
            "created_at": datetime.now().isoformat(),
        }
        package_file.write_text(json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8")
        checklist_file.write_text(self._checklist(package), encoding="utf-8")

        result = {
            "status": package["status"],
            "package_file": str(package_file),
            "checklist_file": str(checklist_file),
            "cover_file": cover_file,
            "source_ebook_file": str(ebook_file),
            "timestamp": datetime.now().isoformat(),
        }
        self.save_step_data(result)
        self.mark_completed()
        print(f"✅ KDP package saved to: {package_file}")
        print(f"✅ Upload checklist saved to: {checklist_file}")
        if not cover_file:
            print("⚠️ Cover still needed. Use the saved prompt at Perchance, then rerun with --cover-background PATH.")
        print(f"Final upload, preview, AI disclosure, pricing, and submission: {KDP_URL}")
        return result

    def _make_cover(self, ebook_file: Path, prompt: str, title: str, author: str) -> str:
        source = os.getenv(
            "AI_BOOK_COVER_SOURCE",
            "pollinations",
        ).strip().lower()
        background = os.getenv("AI_BOOK_COVER_BACKGROUND", "").strip().strip('"')

        if source == "manual" and not background and not self._is_auto():
            print(f"Generate a text-free portrait background using the saved prompt: {PERCHANCE_URL}")
            try:
                background = input(
                    "Downloaded background path (Enter to finish the package without a cover): "
                ).strip().strip('"')
            except (EOFError, StopIteration):
                background = ""

        if background and not Path(background).is_file():
            os.environ.pop("AI_BOOK_COVER_BACKGROUND", None)
            raise FileNotFoundError(f"Cover background not found: {background}")
        if source == "manual" and not background:
            return ""

        output = ebook_file.with_name(f"{ebook_file.stem}_cover.jpg")
        try:
            if source == "perchance" and not background:
                background = generate_perchance_background(
                    prompt,
                    ebook_file.with_name(f"{ebook_file.stem}_background.png"),
                )
            result = create_cover(prompt, title, author, output, background or None)
            print(f"✅ KDP cover saved to: {result}")
            return str(Path(result).resolve())
        except Exception as exc:
            raise RuntimeError(f"{self._image_ai_tool()} cover generation failed: {exc}") from exc
        finally:
            if background:
                # A supplied background belongs to one title; do not reuse it across a series.
                os.environ.pop("AI_BOOK_COVER_BACKGROUND", None)

    def _metadata_suggestions(self, init_data: dict, ebook_data: dict) -> dict[str, list[str]]:
        fallback = self._fallback_metadata(init_data)
        if not self.ai_service:
            return fallback
        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                "Return only a JSON object with two arrays: keywords (exactly 7 specific, accurate "
                "Amazon search phrases) and category_hints (exactly 3 current Amazon KDP Kindle eBook "
                "category paths in English, separated with >, sharing the same first two levels). "
                "Do not include competitor names, claims, promotions, or misleading terms."
            ),
            sections=[
                ("Title", str(init_data.get("book_title", ""))),
                ("Concept", str(init_data.get("book_idea", ""))[:1500]),
                ("Layout", str(init_data.get("layout_content", ""))[:2500]),
                ("Description", str(ebook_data.get("description", ""))[:1200]),
            ],
            max_prompt_tokens=4500,
        )
        try:
            raw = self.ai_service.generate_content(prompt, max_completion_tokens=500)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group(0) if match else raw)
            keywords = self._clean_list(data.get("keywords"), 7, 50)
            categories = self._clean_list(data.get("category_hints"), 3, 100)
            if len(keywords) == 7 and len(categories) == 3:
                return {"keywords": keywords, "category_hints": categories}
        except Exception as exc:
            print(f"⚠️ Using locally derived KDP metadata suggestions: {exc}")
        return fallback

    @staticmethod
    def _fallback_metadata(init_data: dict) -> dict[str, list[str]]:
        text = f"{init_data.get('book_title', '')} {init_data.get('book_idea', '')}"
        stop = {"about", "after", "against", "book", "from", "into", "their", "there", "these", "this", "with"}
        words = []
        for word in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text.lower()):
            if word not in stop and word not in words:
                words.append(word)
        keywords = (words + ["character driven fiction", "original fiction", "literary adventure"])[:7]
        while len(keywords) < 7:
            keywords.append(f"fiction theme {len(keywords) + 1}")
        genre = re.search(
            r"(?im)^\s*(?:\*\*)?genre(?:\*\*)?\s*[:\-]\s*(.+)$",
            str(init_data.get("layout_content", "")),
        )
        primary = genre.group(1).strip("*_ ")[:80] if genre else "Fiction"
        if re.search(r"science fiction|dystop|steampunk|speculative|high tech", primary, re.I):
            categories = [
                "Science Fiction & Fantasy > Science Fiction > Dystopian",
                "Science Fiction & Fantasy > Science Fiction > Steampunk",
                "Science Fiction & Fantasy > Science Fiction > High Tech",
            ]
        else:
            # ponytail: generation is fiction-only; add a nonfiction taxonomy if that scope changes.
            categories = [
                "Literature & Fiction > Genre Fiction > Literary",
                "Literature & Fiction > Genre Fiction > Action & Adventure",
                "Literature & Fiction > Genre Fiction > Coming of Age",
            ]
        return {
            "keywords": keywords,
            "category_hints": categories,
        }

    @staticmethod
    def _clean_list(value: Any, count: int, max_length: int) -> list[str]:
        if not isinstance(value, list):
            return []
        output = []
        for item in value:
            cleaned = re.sub(r"\s+", " ", str(item)).strip()[:max_length]
            if cleaned and cleaned.lower() not in {entry.lower() for entry in output}:
                output.append(cleaned)
        return output[:count]

    @staticmethod
    def _checklist(package: dict) -> str:
        cover = package["cover_file"] or "[CREATE/ADD COVER BEFORE SUBMITTING]"
        return f"""KDP UPLOAD CHECKLIST

1. Open {package['kdp_url']}
2. Create a Kindle eBook and enter these details exactly:
   Title: {package['title']}
   Author: {package['author']}
3. Paste the description, seven keywords, and accurate categories from:
   {Path(package['manuscript_file']).with_name(Path(package['manuscript_file']).stem + '_kdp.json')}
4. Disclose AI-generated text and AI-generated cover art where applicable.
5. Upload manuscript: {package['manuscript_file']}
6. Upload cover: {cover}
7. Run the Online Previewer and fix every blocking or visual issue.
8. Confirm publishing rights, territories, price, and royalty option.
9. Submit for publication.

Amazon KDP does not provide a supported public book-upload API. This final
account action can be completed with --publish-kdp, or reviewed manually here.
"""

    def _text_ai_tool(self) -> str:
        provider = str(getattr(self.ai_service, "provider", "AI")).replace("-oauth", "")
        model = str(getattr(self.ai_service, "writing_model", "")).strip()
        labels = {"openai": "OpenAI", "google": "Google", "groq": "Groq", "minimax": "MiniMax"}
        return " ".join(part for part in (labels.get(provider, provider.title()), model) if part)

    @staticmethod
    def _image_ai_tool() -> str:
        source = os.getenv("AI_BOOK_COVER_SOURCE", "pollinations").strip().lower()
        return {"perchance": "Perchance", "pollinations": "Pollinations"}.get(
            source,
            "AI image generator",
        )

    @staticmethod
    def _cover_is_ai() -> bool:
        return os.getenv("AI_BOOK_COVER_IS_AI", "true").strip().lower() not in {"0", "false", "no"}

    @staticmethod
    def _is_auto() -> bool:
        return os.getenv("AI_BOOK_MODE", "review").strip().lower() == "auto"
