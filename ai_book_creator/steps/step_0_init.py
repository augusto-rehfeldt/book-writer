"""
Step 0: Initialize Book - Concept, layout, and character setup
"""

from datetime import datetime
from typing import Dict, Any
from .base_step import BaseStep


class InitStep(BaseStep):
    def __init__(self, ai_service, project_manager):
        super().__init__(ai_service, project_manager)
        self.step_name = "init"
    
    def should_execute(self) -> bool:
        return not self.is_completed()
    
    def get_step_header(self) -> str:
        return "="*60 + "\n📚 STEP 0: BOOK INITIALIZATION\n" + "="*60
    
    def execute(self) -> Dict[str, Any]:
        # Get concept
        book_idea = self._get_concept()
        
        # Get page count
        page_count = self._get_page_count()

        partial_init_data = {
            "book_idea": book_idea,
            "page_count": page_count,
            "_partial": True
        }
        self.save_step_data(partial_init_data)
        
        # Generate layout
        layout_content = self._generate_layout(book_idea, page_count)
        
        init_data = {
            "book_idea": book_idea,
            "page_count": page_count,
            "layout_content": layout_content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.save_step_data(init_data)
        self.mark_completed()
        return init_data
    
    def _get_concept(self) -> str:
        choice = input("\n1. Provide book idea\n2. Get AI inspiration\n\nChoice (1/2): ").strip()
        
        if choice == "2":
            print("\n🎨 AI Book Ideas:")
            print("-" * 50)
            inspiration = self.ai_service.generate_content(
                "Generate 5 unique book ideas. For each: Title, Genre, Brief premise (2-3 sentences). Number them 1-5.",
                max_completion_tokens=1024,
            )
            print(inspiration)
            print("-" * 50)
            
            selection = input("\nEnter number (1-5) or type your own: ").strip()
            if selection.isdigit() and 1 <= int(selection) <= 5:
                ideas = [i.strip() for i in inspiration.split('\n\n') if i.strip()]
                return ideas[int(selection) - 1] if int(selection) <= len(ideas) else input("Enter your idea: ")
            return selection
        
        return input("\nEnter your book idea: ").strip()
    
    def _get_page_count(self) -> int:
        while True:
            try:
                count = int(input("\nTarget page count: "))
                if count > 0:
                    return count
            except ValueError:
                pass
            print("Enter a valid number")
    
    def _generate_layout(self, book_idea: str, page_count: int) -> str:
        print("\n🔄 Generating book layout...")

        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                f"Create a book layout targeting {page_count} pages. "
                "Be concise but complete."
            ),
            sections=[
                ("Book idea", book_idea),
                (
                    "Required output",
                    "3 potential titles; genre; target audience; 3-5 main themes; setting overview; "
                    "three-act structure; 5-7 main characters with name, role, and brief description.",
                )
            ],
            max_prompt_tokens=2000,
        )
        
        layout = self.ai_service.generate_content(prompt, max_completion_tokens=1400)
        
        print("\n📋 BOOK LAYOUT:")
        print("-" * 50)
        print(layout)
        print("-" * 50)
        
        return layout
