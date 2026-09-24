"""Read the complete manuscript, repair evidenced problems, and record the result."""

import json
import os
from datetime import datetime
from pathlib import Path

from .base_step import BaseStep
from ..core.project_manager import BrokenProjectStateError
from ..models.chapter_model import Chapter
from ..utils import humanizer
from ..utils.editorial import edit_text, story_context, update_continuity
from ..utils.text_utils import calculate_page_count, calculate_word_count, parse_json, save_text, text_chunks, text_digest


class ReviewStep(BaseStep):
    def __init__(self, ai_service, project_manager, output_dir):
        super().__init__(ai_service, project_manager)
        self.step_name = "reviewed"
        self.output_dir = output_dir
        self.words_per_page = 250

    def should_execute(self) -> bool:
        existing = self.get_step_data()
        if not self.is_completed() or not existing.get("analysis"):
            return True
        chapters = self._read_chapters_content(self.project_manager.get_step_data("written").get("chapters", {}))
        return existing.get("source_hashes") != self._source_hashes(chapters)

    def get_step_header(self) -> str:
        return "=" * 60 + "\nSTEP 3: MANUSCRIPT REVIEW\n" + "=" * 60

    @staticmethod
    def _source_hashes(chapters):
        return {key: text_digest(chapter.content) for key, chapter in chapters.items()}

    def _read_chapters_content(self, chapters_dict):
        chapters = {}
        for key, info in chapters_dict.items():
            path = Path(info["filename"])
            if not path.is_file():
                raise BrokenProjectStateError(f"Cannot review missing chapter: {path}")
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                raise BrokenProjectStateError(f"Cannot review empty chapter: {path}")
            chapters[key] = Chapter(info["title"], content, int(info["chapter_number"]),
                                    calculate_word_count(content), str(path))
        return dict(sorted(chapters.items(), key=lambda item: item[1].chapter_number))

    def _combine_chapters_text(self, chapters):
        return "\n\n".join(f"CHAPTER {c.chapter_number}: {c.title}\n{c.content}"
                           for c in chapters.values())

    def _save_chapter(self, key, chapter, content, written):
        save_text(chapter.filename, content)
        chapter.content = content
        chapter.word_count = calculate_word_count(content)
        written["chapters"][key].update(word_count=chapter.word_count, source_hash=text_digest(content))
        if "content" in written["chapters"][key]:
            written["chapters"][key]["content"] = content
        written["total_word_count"] = sum(c["word_count"] for c in written["chapters"].values())
        written["total_pages"] = calculate_page_count(written["total_word_count"], self.words_per_page)
        self.project_manager.set_step_data("written", written)

    def execute(self):
        init_data = self.project_manager.get_step_data("init")
        written = self.project_manager.get_step_data("written")
        self.words_per_page = int(init_data.get("words_per_page", 250))
        chapters = self._read_chapters_content(written.get("chapters", {}))
        if not chapters:
            raise BrokenProjectStateError("No manuscript to review")
        cached = dict(self.get_step_data().get("chapter_reviews", {}))
        memory, ending = "", ""
        ordered = list(chapters.items())
        for index, (key, chapter) in enumerate(ordered):
            context = story_context(init_data, memory)
            context += f"\nPREVIOUS ENDING:\n{ending}"
            # Later chapter edits are checked by the manuscript-wide pass. They
            # must not invalidate completed local work whenever a run resumes.
            context_hash = text_digest(context)
            if index + 1 < len(ordered):
                context += f"\nNEXT CHAPTER OPENING:\n{ordered[index + 1][1].content[:2500]}"
            entry = cached.get(key, {})
            if (entry.get("source_hash") != text_digest(chapter.content)
                    or entry.get("context_hash") != context_hash):
                print(f"Reviewing all of chapter {chapter.chapter_number}: {chapter.title}")
                plot = self.project_manager.get_chapter_plot(chapter.chapter_number) or {}
                instructions = (
                    "Check scene causality, character knowledge, motivation, voice and transitions. "
                    "Repair demonstrated contradictions using the established context. "
                    "Do not add words just to meet the book's page target. "
                    f"Planned chapter events (intentions, not authority over finished prose): {plot.get('plot_outline', '')}"
                )
                revised, findings = edit_text(self.ai_service, chapter.content, instructions, context)
                self._save_chapter(key, chapter, revised, written)
                entry = {"source_hash": text_digest(revised), "context_hash": context_hash,
                         "findings": findings}
                cached[key] = entry
                self.save_step_data({**self.get_step_data(), "chapter_reviews": cached,
                                     "completed": False, "_partial": True})
                self.project_manager.save_project()
            if not entry.get("continuity"):
                entry["continuity"] = update_continuity(self.ai_service, chapter.content, memory, chapter.title)
                self.project_manager.save_project()
            written["chapters"][key]["context_hash"] = text_digest(memory)
            memory, ending = entry["continuity"], chapter.content[-2500:]
            written["chapters"][key].update(continuity=memory)

        full_text = self._combine_chapters_text(chapters)
        analysis = self._generate_analysis(init_data, full_text)
        repair_log = list(self.get_step_data().get("repairs", []))
        # One bounded correction pass. Rejected or unresolved findings remain visible.
        for key, chapter in chapters.items():
            issues = [issue for issue in analysis["issues"]
                      if issue.get("chapter_number") == chapter.chapter_number
                      and isinstance(issue.get("quote"), str) and issue["quote"].strip()
                      and chapter.content.count(issue["quote"]) == 1]
            if not issues:
                continue
            original = chapter.content
            def diagnose(passage, issues=issues):
                return "\n".join(json.dumps(issue, ensure_ascii=False)
                                 for issue in issues if issue["quote"] in passage)
            revised, findings = edit_text(
                self.ai_service, chapter.content,
                "Repair only the quoted manuscript-wide findings supported by the context.",
                story_context(init_data, memory) + "\nMANUSCRIPT FINDINGS:\n" + json.dumps(issues, ensure_ascii=False),
                diagnose=diagnose)
            repair_log.append({"chapter_number": chapter.chapter_number, "findings": findings})
            self._save_chapter(key, chapter, revised, written)
            if revised != original:
                cached[key]["source_hash"] = text_digest(revised)
                cached[key].pop("continuity", None)
            self.save_step_data({**self.get_step_data(), "chapter_reviews": cached, "repairs": repair_log})
            self.project_manager.save_project()

        if self._combine_chapters_text(chapters) != full_text:
            # Refresh continuity from final text, including downstream chapters.
            memory, ending = "", ""
            for key, chapter in chapters.items():
                written["chapters"][key]["context_hash"] = text_digest(memory)
                cached[key]["context_hash"] = text_digest(
                    story_context(init_data, memory) + f"\nPREVIOUS ENDING:\n{ending}")
                memory = update_continuity(self.ai_service, chapter.content, memory, chapter.title)
                written["chapters"][key]["continuity"] = memory
                cached[key]["continuity"] = memory
                ending = chapter.content[-2500:]
            analysis = self._generate_analysis(init_data, self._combine_chapters_text(chapters))

        for key, chapter in chapters.items():
            self._save_chapter(key, chapter, chapter.content, written)
        spread = self._humanness_spread(chapters)
        review_data = {
            "analysis": analysis["analysis"], "unresolved_issues": analysis["issues"],
            "chapter_reviews": cached, "repairs": repair_log,
            "source_hashes": self._source_hashes(chapters), "chapter_count": len(chapters),
            "total_word_count": written["total_word_count"], "total_pages": written["total_pages"],
            "humanness": spread, "timestamp": datetime.now().isoformat(),
        }
        report = ("MANUSCRIPT REVIEW\n\n" + analysis["analysis"] + "\n\nUNRESOLVED FINDINGS\n"
                  + json.dumps(analysis["issues"], ensure_ascii=False, indent=2)
                  + "\n\nSTYLE DIAGNOSTICS (not authorship or quality scores)\n"
                  + json.dumps(spread, indent=2)
                  + f"\n\nLength: {written['total_pages']} pages; target {init_data.get('page_count', 400)}. "
                  "No automatic padding was applied.\n")
        save_text(os.path.join(self.output_dir, "book_analysis.txt"), report)
        # A review can change wording without changing word counts. Export must refresh.
        self.project_manager.reset_steps_from("ebook")
        self.save_step_data(review_data)
        self.mark_completed()
        self.project_manager.save_project()
        return review_data

    def _generate_analysis(self, init_data, full_text):
        """Read every passage, carrying an editorial memo across the entire book."""
        state = dict(self.get_step_data())
        digest = text_digest(full_text + json.dumps(init_data, sort_keys=True))
        cached = state.get("analysis_progress", {})
        if cached.get("source_hash") != digest:
            cached = {}
        memo = cached.get("memo", "")
        issues = list(cached.get("issues", []))
        passages = list(text_chunks(full_text, 12000))
        for index, passage in enumerate(passages, 1):
            if index <= cached.get("passage", 0):
                continue
            prompt = (
                "Review this consecutive manuscript passage in the context of the earlier record. "
                "Track setup/payoff, chronology, motivation, character knowledge, repeated scenes, "
                "voice and the ending. Preserve unresolved plot threads in the record. "
                "Return JSON only: {\"analysis\":\"updated story and editorial record, at most 700 words\", "
                "\"issues\":[{\"chapter_number\":1,\"quote\":\"exact quote from THIS passage\", "
                "\"problem\":\"evidenced contradiction or weakness\",\"fix\":\"specific repair\"}]}. "
                "List only demonstrated defects; an unanswered question, suspense or unresolved "
                "setup in an unfinished book is not a defect. "
                "Treat chapter plans as intentions. A character's mistaken belief is not necessarily "
                "a contradiction. Assess the ending only when it is supplied.\n\n"
                f"CONCEPT: {init_data.get('book_idea', '')}\nPREVIOUS RECORD:\n{memo}\n\n"
                f"PASSAGE {index} OF {len(passages)} "
                f"({'FINAL MANUSCRIPT PASSAGE: assess the ending' if index == len(passages) else 'book continues'}):\n{passage}"
            )
            result = parse_json(self.ai_service.generate_content(
                prompt, model_type="review", max_completion_tokens=3072))
            if (not result or not isinstance(result.get("analysis"), str) or not result["analysis"].strip()
                    or len(result["analysis"].split()) > 1000 or not isinstance(result.get("issues"), list)):
                raise ValueError("Incomplete manuscript analysis; review remains resumable")
            memo = result["analysis"]
            issues.extend(issue for issue in result["issues"] if isinstance(issue, dict)
                          and isinstance(issue.get("chapter_number"), int)
                          and isinstance(issue.get("quote"), str) and issue["quote"].strip()
                          and issue["quote"] in passage
                          and isinstance(issue.get("problem"), str) and isinstance(issue.get("fix"), str))
            state.update(completed=False, analysis_progress={
                "source_hash": digest, "passage": index, "memo": memo, "issues": issues})
            self.save_step_data(state)
            self.project_manager.save_project()
        return {"analysis": memo, "issues": issues}

    def _humanness_spread(self, chapters):
        if not humanizer.enabled():
            return {}
        report = humanizer.book_report([c.content for c in chapters.values()])
        report["chapter_scores"] = [humanizer.local_score(c.content)["score"] for c in chapters.values()]
        return report
