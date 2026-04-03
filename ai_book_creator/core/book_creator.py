"""
Main AIBookCreator class - orchestrates the entire book creation process
"""

import os
import json
import sys
import shutil
import glob

from .project_manager import ProjectManager
from .project_manager import BrokenProjectStateError
from ..services.ai_service import AIService, DailyTokenBudgetExceeded, UsageLimitExceeded
from ..steps.step_0_init import InitStep
from ..steps.step_1_structure import StructureStep
from ..steps.step_2_write import WriteStep
from ..steps.step_3_review import ReviewStep
from ..steps.step_4_ebook import EbookStep
from ..utils.glossary_manager import GlossaryManager
from ..utils.text_utils import calculate_page_count


class AIBookCreator:
    def __init__(self, output_dir=None):
        """Initialize the AI Book Creator with all necessary components"""
        # Force the working directory to the repository root to avoid relative path bugs
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.chdir(repo_root)
        
        self.output_dir = output_dir or "book_output"
        os.makedirs(self.output_dir, exist_ok=True)
        self.config_path = os.getenv(
            "AI_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "..", "config", "ai_config_google.json"),
        )
        
        # Load configuration
        self.config = self._load_config()

        # Initialize core services
        self.project_manager = ProjectManager(self.output_dir)
        self.ai_service = AIService(
            config_path=self.config_path,
            usage_state_path=os.path.join(self.output_dir, "ai_usage_state.json")
        )
        self.glossary_manager = GlossaryManager(self.output_dir)
        
        # Initialize step processors
        self.steps = {
            0: InitStep(self.ai_service, self.project_manager),
            1: StructureStep(self.ai_service, self.project_manager, self.glossary_manager),
            2: WriteStep(self.ai_service, self.project_manager, self.glossary_manager, self.output_dir),
            3: ReviewStep(self.ai_service, self.project_manager, self.output_dir),
            4: EbookStep(self.ai_service, self.project_manager, self.output_dir),
        }
        
        print("✅ AI Book Creator initialized successfully!")

    def _load_config(self):
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def create_book(self):
        """Main method to create the complete book with full resumability"""
        print("🚀 Starting AI Book Creation Process")
        print("=" * 60)
        
        # Load any existing project data
        self.project_manager.load_project()
        self._sync_runtime_budget_state()
        
        while True:
            # Print current Series Status if initialized
            init_data = self.project_manager.get_step_data("init")
            if init_data:
                series_mode = init_data.get("series_mode", False)
                series_book_count = int(init_data.get("series_book_count", 1))
                current_book = int(init_data.get("current_book", 1))
                if series_mode:
                    print(f"\n📚 SERIES STATUS: Working on Book {current_book} of {series_book_count}")
                else:
                    print("\n📘 STATUS: Single Book Project")
            
            recovery_plan = self.project_manager.get_recovery_plan()
            if recovery_plan["state_status"] != "healthy":
                print("\nℹ️ Saved project recovery status.")
                print(recovery_plan["message"])
                if recovery_plan.get("state_status") == "broken" and recovery_plan.get("broken_steps"):
                    broken_steps = ", ".join(
                        self._format_step_label(step) for step in recovery_plan["broken_steps"]
                    )
                    print(f"Broken steps: {broken_steps}")
                if recovery_plan.get("restart_step_label"):
                    print(f"Recommended restart point: {recovery_plan['restart_step_label']}")
                if recovery_plan["state_status"] == "broken":
                    if not self._prompt_repair_and_restart(recovery_plan):
                        print("Leaving the saved state unchanged. You can run `python utils.py repair book_output` later.")
                        return
            
            if self.ai_service.has_budget_pause():
                self._mark_budget_pause()
                self.project_manager.save_project()
                print(self._budget_pause_message())
                print("The current progress has been cached. Rerun later to continue, or stop here if you are done for today.")
                return
            
            try:
                # Execute each step in sequence
                for step_num in range(len(self.steps)):
                    step_processor = self.steps[step_num]
                    
                    if step_processor.should_execute():
                        print(f"\n{step_processor.get_step_header()}")
                        step_processor.execute()
                        self.project_manager.save_project()
                        if self.ai_service.has_budget_pause():
                            self._mark_budget_pause()
                            self.project_manager.save_project()
                            print(self._budget_pause_message())
                            print("The current progress has been cached. Rerun later to continue, or stop here if you are done for today.")
                            return
                    else:
                        print(f"\n⏩ Step {step_num}: {step_processor.__class__.__name__} - Already completed")
                
                if self.ai_service.has_budget_pause():
                    self._mark_budget_pause()
                    self.project_manager.save_project()
                    print(self._budget_pause_message())
                    print("The current progress has been cached. Rerun later to continue, or stop here if you are done for today.")
                    return

                # Generate final glossary
                self.glossary_manager.generate_final_glossary(self.project_manager.book_data)
                
                self._print_completion_summary()
                self._validate_book_length()

                # --- AUTOMATIC SERIES CONTINUATION ---
                init_data = self.project_manager.get_step_data("init")
                series_mode = init_data.get("series_mode", False)
                series_book_count = int(init_data.get("series_book_count", 1))
                current_book = int(init_data.get("current_book", 1))

                if not series_mode or current_book >= series_book_count:
                    if series_mode and current_book >= series_book_count:
                        print(f"\n🏆 Entire {series_book_count}-book series creation complete!")
                    break

                self._advance_to_next_book(current_book)
                
            except (DailyTokenBudgetExceeded, UsageLimitExceeded) as e:
                pause_bucket = getattr(e, "bucket", getattr(e, "metric", ""))
                self._mark_budget_pause(str(e), pause_bucket, e.tokens_used, e.token_limit, e.model_name)
                self.project_manager.save_project()
                print(f"\n⏸️  {e}")
                print("The current progress has been cached. Rerun later to continue, or stop here if you are done for today.")
                break
            except BrokenProjectStateError as e:
                self.project_manager.save_project()
                print(f"\n⚠️  {e}")
                if getattr(e, "latest_valid_step", ""):
                    print(f"Latest correct step: {self._format_step_label(e.latest_valid_step)}")
                if getattr(e, "restart_step", ""):
                    print(f"Rewrite from: {self._format_step_label(e.restart_step)}")
                if getattr(e, "broken_steps", []):
                    broken_steps = ", ".join(self._format_step_label(step) for step in e.broken_steps)
                    print(f"Broken steps: {broken_steps}")
                if self._prompt_repair_and_restart(
                    {
                        "restart_step": getattr(e, "restart_step", ""),
                        "restart_step_label": self._format_step_label(getattr(e, "restart_step", "")),
                    }
                ):
                    continue
                print("The broken downstream state has been preserved so you can restart from the last good step.")
                break
            except KeyboardInterrupt:
                print("\n\n⏹️ Process interrupted by user.")
                self.project_manager.save_project()
                print("📁 Progress saved. You can resume later by running the script again.")
                break
            except Exception as e:
                print(f"\n❌ An unexpected error occurred: {e}")
                import traceback
                traceback.print_exc()
                self.project_manager.save_project()
                print("📁 Progress saved despite error. You can try to resume later.")
                break

    def _advance_to_next_book(self, current_book: int):
        """Archives the finished book's files and prompts the AI for the next book layout."""
        print("\n" + "=" * 60)
        print(f"📚 PREPARING NEXT BOOK IN SERIES: Book {current_book + 1}")
        print("=" * 60)

        # 1. Archive current book's working files (Leaves EPUB intact in 'ebook' folder)
        archive_dir = os.path.join(self.output_dir, f"book_{current_book}_archive")
        os.makedirs(archive_dir, exist_ok=True)
        
        # Move chapters
        for f in glob.glob(os.path.join(self.output_dir, "chapter_*.txt")):
            shutil.move(f, os.path.join(archive_dir, os.path.basename(f)))
            
        # Move checkpoints
        for f in glob.glob(os.path.join(self.output_dir, "checkpoint_*.json")):
            shutil.move(f, os.path.join(archive_dir, os.path.basename(f)))
            
        # Move analysis & glossary txt (the JSON glossary stays active for continuity)
        for txt_file in ["book_analysis.txt", "book_glossary.txt"]:
            f_path = os.path.join(self.output_dir, txt_file)
            if os.path.exists(f_path):
                shutil.move(f_path, os.path.join(archive_dir, txt_file))
                
        # 2. Gather context for Book N+1
        init_data = self.project_manager.get_step_data("init")
        series_layout = init_data.get("series_layout_content", "")
        book_idea = init_data.get("book_idea", "")
        page_count = init_data.get("page_count", 300)
        
        reviewed_data = self.project_manager.get_step_data("reviewed")
        previous_analysis = reviewed_data.get("analysis", "")
        
        previous_summaries = init_data.get("previous_books_summary", "")
        if previous_analysis:
            previous_summaries += f"\n\n--- Book {current_book} Summary & Analysis ---\n{previous_analysis}"
            init_data["previous_books_summary"] = previous_summaries

        print(f"\n🔄 Generating layout for Book {current_book + 1}...")
        
        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                f"Create a book layout for Book {current_book + 1} of the series. "
                f"Target {page_count} pages. Build upon the series layout and ensure the story advances appropriately. "
                "Incorporate developments from the previous books. Be concise but complete."
            ),
            sections=[
                ("Overall Series Idea", book_idea),
                ("Series Layout", series_layout),
                ("Previous Books Context", previous_summaries if previous_summaries else "Follow the series layout progression."),
                (
                    "Required output",
                    f"3 potential titles for Book {current_book + 1}; genre; target audience; 3-5 main themes for this book; setting overview; "
                    f"three-act structure for Book {current_book + 1}; 5-7 main characters with name, role, and brief description."
                )
            ],
            max_prompt_tokens=4000,
            section_token_caps={
                "Overall Series Idea": 300,
                "Series Layout": 1500,
                "Previous Books Context": 1500,
                "Required output": 250,
            }
        )
        
        new_layout = self.ai_service.generate_content(prompt, max_completion_tokens=1500)
        
        print(f"\n📋 BOOK {current_book + 1} LAYOUT:")
        print("-" * 50)
        print(new_layout)
        print("-" * 50)
        
        # 3. Update Init Data
        init_data["current_book"] = current_book + 1
        init_data["layout_content"] = new_layout
        self.project_manager.set_step_data("init", init_data)
        
        # 4. Clear Downstream Steps
        for step_name in ["structure", "written", "reviewed", "ebook"]:
            if step_name in self.project_manager.book_data:
                del self.project_manager.book_data[step_name]
                
        # 5. Save Project
        self.project_manager.save_project()
        print(f"\n✅ Ready to start Book {current_book + 1}!")

    def _sync_runtime_budget_state(self):
        """Restore any persisted budget pause notice into runtime state."""
        runtime = self.project_manager.book_data.get("_runtime", {})
        if runtime.get("status") == "paused_for_budget":
            if self.ai_service.has_budget_pause():
                self.ai_service._budget_pause_requested = True
                if runtime.get("message"):
                    self.ai_service._budget_pause_reason = runtime.get("message", "")
            else:
                self.project_manager.book_data.pop("_runtime", None)

    def _mark_budget_pause(
        self,
        message: str = "",
        bucket: str = "",
        tokens_used: int = 0,
        token_limit: int = 0,
        model_name: str = "",
    ):
        """Persist a budget pause note so the next run can resume deliberately."""
        message = message or self.ai_service.get_budget_pause_message() or "The AI request budget paused the run."
        self.project_manager.book_data["_runtime"] = {
            "status": "paused_for_budget",
            "message": message,
            "bucket": bucket,
            "tokens_used": tokens_used,
            "token_limit": token_limit,
            "model_name": model_name,
        }

    def _budget_pause_message(self) -> str:
        message = self.ai_service.get_budget_pause_message()
        if message:
            return f"⏸️  {message}"
        return "⏸️  The AI request budget paused the run."
    
    def _print_completion_summary(self):
        """Print the final completion summary"""
        init_data = self.project_manager.book_data.get("init", {})
        written_data = self.project_manager.book_data.get("written", {})
        current_book = init_data.get("current_book", 1)
        
        print("\n" + "=" * 60)
        if init_data.get("series_mode"):
            print(f"🎉 BOOK {current_book} CREATION COMPLETED!")
        else:
            print("🎉 BOOK CREATION COMPLETED!")
        print("=" * 60)
        print(f"📁 Output directory: {self.output_dir}")
        if init_data.get("series_mode"):
            print(f"📚 Series size: {int(init_data.get('series_book_count', 0))} books")
        print(f"📖 Total chapters: {len(written_data.get('chapters', {}))}")
        print(f"📊 Total words: {written_data.get('total_word_count', 0):,}")
        print(f"📄 Estimated pages: {written_data.get('total_pages', 0)}")
        
        # Show glossary stats if available
        glossary_stats = self.glossary_manager.get_glossary_stats()
        if glossary_stats:
            print(f"👤 Characters: {glossary_stats['characters']}")
            print(f"📍 Locations: {glossary_stats['locations']}")
            print(f"🎯 Concepts: {glossary_stats['concepts']}")

    def _validate_book_length(self):
        """Validate if the total book length meets the minimum requirement."""
        init_data = self.project_manager.book_data.get("init", {})
        min_book_pages = init_data.get("page_count", 300)
        written_data = self.project_manager.book_data.get("written", {})
        total_pages = written_data.get("total_pages", 0)

        print("\n" + "=" * 60)
        print("📚 LENGTH VALIDATION")
        print("=" * 60)
        print(f"Target: {min_book_pages} pages")
        print(f"Actual: {total_pages} pages")
        if init_data.get("series_mode"):
            print(f"Series books: {int(init_data.get('series_book_count', 0))}")

        if total_pages < min_book_pages:
            print(f"⚠️ Book is shorter than target")
        else:
            print("✅ Target met")

    def _format_step_label(self, step_name: str) -> str:
        labels = {
            "init": "Step 0: Initialization",
            "structure": "Step 1: Chapter Structure",
            "written": "Step 2: Writing Chapters",
            "reviewed": "Step 3: Review",
            "ebook": "Step 4: Ebook Export",
        }
        return labels.get(step_name, step_name)

    def _prompt_repair_and_restart(self, recovery_plan: dict) -> bool:
        """Ask whether to repair a broken saved project and restart from the suggested step."""
        restart_step = recovery_plan.get("restart_step", "")
        restart_label = recovery_plan.get("restart_step_label", "")
        if not restart_step:
            return False

        prompt = f"Repair the project and restart from {restart_label}? [Y/n]: "
        while True:
            try:
                response = input(prompt).strip().lower()
            except EOFError:
                return False

            if response in ("", "y", "yes"):
                self.project_manager.reset_steps_from(restart_step)
                self.project_manager.save_project()
                print(f"Repaired project. Restarting from {restart_label}.")
                return True
            if response in ("n", "no"):
                return False
            print("Please answer yes or no.")