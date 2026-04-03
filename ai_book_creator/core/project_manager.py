"""
Project Manager - handles saving and loading project state
"""

import os
import json
from datetime import datetime
from typing import Dict, Any, Optional


STEP_SEQUENCE = [
    ("init", "Step 0: Initialization"),
    ("structure", "Step 1: Chapter Structure"),
    ("written", "Step 2: Writing Chapters"),
    ("reviewed", "Step 3: Review"),
    ("ebook", "Step 4: Ebook Export"),
]


class BrokenProjectStateError(RuntimeError):
    """Raised when persisted project data is internally inconsistent."""

    def __init__(
        self,
        message: str,
        latest_valid_step: str = "",
        restart_step: str = "",
        broken_steps: Optional[list[str]] = None,
    ):
        super().__init__(message)
        self.latest_valid_step = latest_valid_step
        self.restart_step = restart_step
        self.broken_steps = broken_steps or []


class ProjectManager:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.project_file = os.path.join(output_dir, "project_data.json")
        self.book_data = {}
    
    def load_project(self) -> bool:
        """Load project data from the master JSON file if it exists"""
        if os.path.exists(self.project_file):
            print(f"\n🔄 Found existing project file: {self.project_file}. Attempting to resume.")
            try:
                with open(self.project_file, 'r', encoding='utf-8') as f:
                    self.book_data = json.load(f)
                print("✅ Project data loaded successfully. Will resume from the last completed step.")
                return True
            except Exception as e:
                print(f"⚠️ Could not load project file: {e}. Starting fresh.")
                self.book_data = {}
        return False
    
    def save_project(self):
        """Save the entire project data state to a single JSON file"""
        try:
            # Add metadata
            self.book_data["_metadata"] = {
                "last_updated": datetime.now().isoformat(),
                "version": "2.0.0"
            }
            
            with open(self.project_file, 'w', encoding='utf-8') as f:
                json.dump(self.book_data, f, indent=4, ensure_ascii=False)
            print(f"💾 Project state saved successfully to {self.project_file}")
        except Exception as e:
            print(f"❌ Error saving project data: {e}")
    
    def save_checkpoint(self, checkpoint_name: str, data: Dict[str, Any]):
        """Save a checkpoint for partial progress"""
        checkpoint_file = os.path.join(self.output_dir, f"checkpoint_{checkpoint_name}.json")
        try:
            checkpoint_data = {
                "timestamp": datetime.now().isoformat(),
                "data": data
            }
            with open(checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(checkpoint_data, f, indent=4, ensure_ascii=False)
            print(f"📍 Checkpoint saved: {checkpoint_file}")
        except Exception as e:
            print(f"❌ Error saving checkpoint: {e}")
    
    def get_step_data(self, step_name: str) -> Dict[str, Any]:
        """Get data for a specific step"""
        return self.book_data.get(step_name, {})
    
    def set_step_data(self, step_name: str, data: Dict[str, Any]):
        """Set data for a specific step"""
        self.book_data[step_name] = data
    
    def is_step_completed(self, step_name: str, completion_key: str = "completed") -> bool:
        """Check if a step is marked as completed"""
        step_data = self.get_step_data(step_name)
        return step_data.get(completion_key, False)
    
    def mark_step_completed(self, step_name: str, completion_key: str = "completed"):
        """Mark a step as completed"""
        if step_name not in self.book_data:
            self.book_data[step_name] = {}
        self.book_data[step_name][completion_key] = True
    
    def get_chapter_plot(self, chapter_number: int) -> Optional[Dict[str, Any]]:
        """Retrieve the structured plot metadata for a specific chapter number."""
        structure_data = self.book_data.get("structure", {})
        chapter_plots = structure_data.get("chapter_plots", {})
        for plot in chapter_plots.values():
            if plot.get("chapter_number") == chapter_number:
                return plot
        return None

    def get_project_summary(self) -> Dict[str, Any]:
        """Get a summary of project progress"""
        recovery = self.get_recovery_plan()
        summary = {
            "steps_completed": [],
            "healthy_steps_completed": [],
            "total_chapters": 0,
            "total_words": 0,
            "estimated_pages": 0,
            "state_status": recovery["state_status"],
            "latest_valid_step": recovery["latest_valid_step_label"],
            "recommended_restart_step": recovery["restart_step_label"],
            "broken_steps": recovery["broken_steps"],
        }
        
        # Check which steps are completed
        step_names = [
            "concept",
            "layout",
            "character_layout",
            "structure",
            "plots",
            "refined",
            "written",
            "reviewed",
            "ebook",
            "fixed",
        ]
        for step in step_names:
            if step in self.book_data:
                summary["steps_completed"].append(step)

        summary["healthy_steps_completed"] = recovery["healthy_steps"]
        
        # Get writing statistics if available
        if "written" in self.book_data:
            written_data = self.book_data["written"]
            summary["total_chapters"] = len(written_data.get("chapters", {}))
            summary["total_words"] = written_data.get("total_word_count", 0)
            summary["estimated_pages"] = written_data.get("total_pages", 0)
        
        return summary

    def get_recovery_plan(self) -> Dict[str, Any]:
        """Return a recovery summary for the currently saved project state."""
        healthy_steps = []
        broken_steps = []
        first_gap_index = None
        state_status = "healthy"

        for index, (step_name, _) in enumerate(STEP_SEQUENCE):
            step_data = self.get_step_data(step_name)
            completed = bool(step_data.get("completed"))
            valid = self._is_step_valid(step_name)

            if completed:
                if first_gap_index is not None:
                    state_status = "broken"
                    broken_steps.append(step_name)
                    continue

                if not valid:
                    state_status = "broken"
                    broken_steps.append(step_name)
                    first_gap_index = index
                    continue

                healthy_steps.append(step_name)
                continue

            if first_gap_index is None:
                first_gap_index = index

        latest_valid_step = healthy_steps[-1] if healthy_steps else ""

        if state_status != "broken":
            complete_count = len(healthy_steps)
            if complete_count < len(STEP_SEQUENCE):
                state_status = "in_progress"
            else:
                state_status = "healthy"

        if state_status == "broken":
            restart_step = STEP_SEQUENCE[first_gap_index][0] if first_gap_index is not None else ""
        elif first_gap_index is not None and first_gap_index < len(STEP_SEQUENCE):
            restart_step = STEP_SEQUENCE[first_gap_index][0]
        else:
            restart_step = ""

        latest_valid_label = self._step_label(latest_valid_step)
        restart_label = self._step_label(restart_step)
        message = self._build_recovery_message(
            latest_valid_label,
            restart_label,
            broken_steps,
            state_status,
        )

        return {
            "is_healthy": state_status != "broken",
            "state_status": state_status,
            "healthy_steps": healthy_steps,
            "latest_valid_step": latest_valid_step,
            "latest_valid_step_label": latest_valid_label,
            "restart_step": restart_step,
            "restart_step_label": restart_label,
            "broken_steps": broken_steps,
            "message": message,
        }

    def reset_steps_from(self, step_name: str) -> None:
        """Clear a step and all later steps from the saved project state."""
        if not step_name:
            return

        start_index = None
        for index, (candidate, _) in enumerate(STEP_SEQUENCE):
            if candidate == step_name:
                start_index = index
                break

        if start_index is None:
            return

        for candidate, _ in STEP_SEQUENCE[start_index:]:
            self.book_data.pop(candidate, None)

    def _is_step_valid(self, step_name: str) -> bool:
        step_data = self.get_step_data(step_name)
        if not step_data.get("completed"):
            return False

        if step_name == "init":
            if not bool(step_data.get("book_idea")) or not bool(step_data.get("layout_content")):
                return False

            if step_data.get("series_mode") or step_data.get("scope_type") == "series":
                if int(step_data.get("series_book_count", 0)) < 2:
                    return False
                if not bool(step_data.get("series_layout_content")):
                    return False

            return True

        if step_name == "structure":
            chapter_plots = step_data.get("chapter_plots", {})
            return isinstance(chapter_plots, dict) and len(chapter_plots) > 0

        if step_name == "written":
            chapters = step_data.get("chapters", {})
            return (
                isinstance(chapters, dict)
                and len(chapters) > 0
                and int(step_data.get("total_word_count", 0)) > 0
            )

        if step_name == "reviewed":
            analysis = step_data.get("analysis", "")
            written_data = self.get_step_data("written")
            written_chapters = written_data.get("chapters", {})
            return (
                bool(analysis)
                and self._is_step_valid("written")
                and int(step_data.get("chapter_count", 0)) == len(written_chapters)
                and int(step_data.get("total_word_count", 0)) == int(written_data.get("total_word_count", 0))
            )

        if step_name == "ebook":
            output_file = step_data.get("output_file", "")
            prompt_file = step_data.get("prompt_file", "")
            written_data = self.get_step_data("written")
            reviewed_data = self.get_step_data("reviewed")
            written_chapters = written_data.get("chapters", {})
            source_chapter_count = len(written_chapters)
            source_written_words = int(written_data.get("total_word_count", 0))
            source_reviewed_words = int(reviewed_data.get("total_word_count", source_written_words))
            return (
                bool(output_file)
                and os.path.exists(output_file)
                and bool(prompt_file)
                and os.path.exists(prompt_file)
                and self._is_step_valid("written")
                and self._is_step_valid("reviewed")
                and int(step_data.get("source_chapter_count", 0)) == source_chapter_count
                and int(step_data.get("source_written_word_count", 0)) == source_written_words
                and int(step_data.get("source_reviewed_word_count", 0)) == source_reviewed_words
            )

        return True

    def _step_label(self, step_name: str) -> str:
        for candidate, label in STEP_SEQUENCE:
            if candidate == step_name:
                return label
        return ""

    def _build_recovery_message(
        self,
        latest_valid_label: str,
        restart_label: str,
        broken_steps: list[str],
        state_status: str,
    ) -> str:
        if state_status == "healthy":
            return f"Project state is healthy. Latest correct step: {latest_valid_label or 'none'}."

        if state_status == "in_progress":
            if latest_valid_label and restart_label:
                return (
                    f"Project is in progress. Latest correct step: {latest_valid_label}. "
                    f"Next step to run: {restart_label}."
                )
            if latest_valid_label:
                return f"Project is in progress. Latest correct step: {latest_valid_label}."
            return "Project is in progress. Start from Step 0: Initialization."

        broken_labels = ", ".join(self._step_label(step) for step in broken_steps if self._step_label(step))
        if latest_valid_label and restart_label:
            return (
                f"Latest correct step: {latest_valid_label}. "
                f"Rewrite from {restart_label} and clear downstream steps. "
                f"Broken steps: {broken_labels}."
            )
        if latest_valid_label:
            return f"Latest correct step: {latest_valid_label}. Rewrite from the next step and clear downstream steps."
        return (
            f"Project state is broken before any step completed. "
            f"Start again from {STEP_SEQUENCE[0][1]}."
        )
