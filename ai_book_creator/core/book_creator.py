"""
Main AIBookCreator class - orchestrates the entire book creation process
"""

import os
import json
import sys

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
    def __init__(self):
        """Initialize the AI Book Creator with all necessary components"""
        self.output_dir = "book_output"
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
            
        except (DailyTokenBudgetExceeded, UsageLimitExceeded) as e:
            pause_bucket = getattr(e, "bucket", getattr(e, "metric", ""))
            self._mark_budget_pause(str(e), pause_bucket, e.tokens_used, e.token_limit, e.model_name)
            self.project_manager.save_project()
            print(f"\n⏸️  {e}")
            print("The current progress has been cached. Rerun later to continue, or stop here if you are done for today.")
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
                return self.create_book()
            print("The broken downstream state has been preserved so you can restart from the last good step.")
        except KeyboardInterrupt:
            print("\n\n⏹️ Process interrupted by user.")
            self.project_manager.save_project()
            print("📁 Progress saved. You can resume later by running the script again.")
        except Exception as e:
            print(f"\n❌ An unexpected error occurred: {e}")
            import traceback
            traceback.print_exc()
            self.project_manager.save_project()
            print("📁 Progress saved despite error. You can try to resume later.")

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
        
        print("\n" + "=" * 60)
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
