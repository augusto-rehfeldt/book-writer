"""
Glossary Manager - handles character, location, concept, and term tracking
"""

import os
import json
import re
from datetime import datetime
from typing import Dict, List, Any


class GlossaryManager:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.glossary_file = os.path.join(output_dir, "glossary.json")
        self.glossary = self._initialize_glossary()
    
    def _initialize_glossary(self) -> Dict[str, Any]:
        """Initialize or load existing glossary"""
        default_glossary = {
            "characters": {},
            "locations": {},
            "concepts": {}
        }
        
        if os.path.exists(self.glossary_file):
            try:
                with open(self.glossary_file, 'r', encoding='utf-8') as f:
                    loaded_glossary = json.load(f)
                # Merge with default structure to ensure all keys exist
                for key in default_glossary:
                    if key not in loaded_glossary:
                        loaded_glossary[key] = default_glossary[key]
                return loaded_glossary
            except Exception as e:
                print(f"Warning: Could not load existing glossary: {e}. Starting fresh.")
        
        return default_glossary
    
    def save_glossary(self):
        """Save the glossary to file"""
        try:
            with open(self.glossary_file, 'w', encoding='utf-8') as f:
                json.dump(self.glossary, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving glossary: {e}")
    
    def add_character(self, name: str, description: str):
        """Add a character to the glossary"""
        self.glossary["characters"][name] = {
            "name": name,
            "description": description
        }
    
    def add_location(self, name: str, description: str):
        """Add a location to the glossary"""
        self.glossary["locations"][name] = {
            "name": name,
            "description": description
        }
    
    def add_concept(self, name: str, description: str):
        """Add a concept to the glossary"""
        self.glossary["concepts"][name] = {
            "name": name,
            "description": description
        }
    

    def update_entry(self, category: str, name: str, updates: Dict[str, Any]):
        """Update an existing glossary entry"""
        if category in self.glossary and name in self.glossary[category]:
            # Deep merge dictionaries
            for key, value in updates.items():
                if isinstance(value, list):
                    # Append to lists, avoiding duplicates
                    if key in self.glossary[category][name] and isinstance(self.glossary[category][name][key], list):
                        for item in value:
                            if item not in self.glossary[category][name][key]:
                                self.glossary[category][name][key].append(item)
                    else:
                        self.glossary[category][name][key] = value
                elif isinstance(value, dict):
                    # Merge dictionaries
                    if key in self.glossary[category][name] and isinstance(self.glossary[category][name][key], dict):
                        self.glossary[category][name][key].update(value)
                    else:
                        self.glossary[category][name][key] = value
                else:
                    # Overwrite other values
                    self.glossary[category][name][key] = value
            print(f"🔄 Updated {category.rstrip('s')}: {name}")
            return True
        return False
    
    def extract_from_content(self, content: str, chapter_title: str = "", ai_service=None) -> Dict[str, List[str]]:
        """Extract characters, locations, and concepts from content using AI"""
        if not ai_service:
            return {"characters": [], "locations": [], "concepts": []}
        
        extraction_prompt = f"""Extract key elements from this chapter:

CHAPTER: {chapter_title}
CONTENT: {content[:2000]}...

Extract:
1. CHARACTERS: Main/supporting characters with brief description
2. LOCATIONS: Important places with brief description  
3. CONCEPTS: Key themes, objects, or systems with brief description

Return JSON: {{"characters": [{{"name": "", "description": ""}}], "locations": [...], "concepts": [...]}}
Only include truly important elements."""

        try:
            result = ai_service.generate_content(extraction_prompt)
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {"characters": [], "locations": [], "concepts": []}
        except Exception as e:
            return {"characters": [], "locations": [], "concepts": []}
    
    def auto_populate_from_chapter(self, chapter_content: str, chapter_title: str, ai_service):
        """Automatically populate glossary from a chapter"""
        extracted = self.extract_from_content(chapter_content, chapter_title, ai_service)
        
        for char in extracted.get("characters", []):
            if char.get("name") and char["name"] not in self.glossary["characters"]:
                self.add_character(char["name"], char.get("description", ""))

        for loc in extracted.get("locations", []):
            if loc.get("name") and loc["name"] not in self.glossary["locations"]:
                self.add_location(loc["name"], loc.get("description", ""))

        for concept in extracted.get("concepts", []):
            if concept.get("name") and concept["name"] not in self.glossary["concepts"]:
                self.add_concept(concept["name"], concept.get("description", ""))
        
        self.save_glossary()


    def generate_final_glossary(self, book_data: Dict[str, Any]):
        """Generate a comprehensive final glossary document"""
        glossary_content = self._format_glossary_content()
        
        # Save as formatted text file
        output_file = os.path.join(self.output_dir, "book_glossary.txt")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("BOOK GLOSSARY\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Book: {book_data.get('concept', {}).get('book_idea', 'Unknown')}\n\n")
            f.write(glossary_content)
        
        print(f"📚 Final glossary saved to: {output_file}")
    
    def _format_glossary_content(self) -> str:
        """Format glossary into readable text"""
        content = ""
        
        if self.glossary["characters"]:
            content += "CHARACTERS\n" + "-" * 20 + "\n\n"
            for name, data in sorted(self.glossary["characters"].items()):
                content += f"• {name}: {data['description']}\n\n"
        
        if self.glossary["locations"]:
            content += "\nLOCATIONS\n" + "-" * 20 + "\n\n"
            for name, data in sorted(self.glossary["locations"].items()):
                content += f"• {name}: {data['description']}\n\n"
        
        if self.glossary["concepts"]:
            content += "\nCONCEPTS\n" + "-" * 20 + "\n\n"
            for name, data in sorted(self.glossary["concepts"].items()):
                content += f"• {name}: {data['description']}\n\n"
        
        return content
    
    def get_glossary_stats(self) -> Dict[str, int]:
        """Get statistics about glossary content"""
        return {
            "characters": len(self.glossary["characters"]),
            "locations": len(self.glossary["locations"]),
            "concepts": len(self.glossary["concepts"])
        }
    
    def get_context_for_writing(self, relevant_terms: List[str] = None) -> str:
        """Get relevant glossary context for writing"""
        if not relevant_terms:
            return ""
        
        context = "RELEVANT GLOSSARY CONTEXT:\n"
        
        for term in relevant_terms:
            if term in self.glossary["characters"]:
                char = self.glossary["characters"][term]
                context += f"• {term}: {char['description']}\n"
            elif term in self.glossary["locations"]:
                loc = self.glossary["locations"][term]
                context += f"• {term}: {loc['description']}\n"
            elif term in self.glossary["concepts"]:
                concept = self.glossary["concepts"][term]
                context += f"• {term}: {concept['description']}\n"
        
        return context + "\n"