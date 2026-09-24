"""
Glossary Manager - handles character, location, concept, and term tracking
"""

import os
import json
import re
import random
from datetime import datetime
from typing import Dict, List, Any, Optional
from .text_utils import parse_json, save_text, text_chunks

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
            save_text(self.glossary_file, json.dumps(self.glossary, indent=2, ensure_ascii=False),
                      keep_history=False)
        except Exception as e:
            print(f"Error saving glossary: {e}")
            raise
    
    # ponytail: three one-line wrappers kept as the call sites read better.
    def add_character(self, name: str, description: str) -> None:
        self._add("characters", name, description)

    def add_location(self, name: str, description: str) -> None:
        self._add("locations", name, description)

    def add_concept(self, name: str, description: str) -> None:
        self._add("concepts", name, description)

    def _add(self, category: str, name: str, description: str) -> None:
        self.glossary[category][name] = {"name": name, "description": description}


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
        
        collected = {"characters": {}, "locations": {}, "concepts": {}}
        for passage in text_chunks(content):
            prompt = (
                "Extract important named characters, locations and concepts from this passage. "
                "Use the exact names on the page; never rename placeholders or invent identities. "
                "Return JSON with characters, locations, concepts arrays, each containing objects "
                "with string name and description. Preserve earlier established facts when updating "
                "a description; distinguish beliefs and plans from actual events.\n"
                f"EXISTING GLOSSARY:\n{self._format_glossary_content()}\n"
                f"EARLIER PASSAGES:\n{json.dumps(collected, ensure_ascii=False)}\n"
                f"CHAPTER: {chapter_title}\nPASSAGE:\n{passage}"
            )
            result = parse_json(ai_service.generate_content(prompt, model_type="review", max_completion_tokens=2048))
            if not isinstance(result, dict):
                raise ValueError("Invalid glossary extraction; previous glossary retained")
            for category in collected:
                # Models omit empty categories, send null, or key entries by name.
                entries = result.get(category) or []
                if isinstance(entries, dict):
                    entries = [v if isinstance(v, dict) else {"name": k, "description": v}
                               for k, v in entries.items()]
                if not isinstance(entries, list):
                    raise ValueError(f"Invalid glossary category: {category}")
                for entry in entries:
                    if (not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                            or not entry["name"].strip() or not isinstance(entry.get("description"), str)):
                        continue  # one malformed entry must not abort the chapter
                    collected[category][entry["name"]] = entry
        return {key: list(entries.values()) for key, entries in collected.items()}
    
    def auto_populate_from_chapter(self, chapter_content: str, chapter_title: str, ai_service):
        """Automatically populate glossary from a chapter, using name pools when possible."""
        extracted = self.extract_from_content(chapter_content, chapter_title, ai_service)

        for category in ("characters", "locations", "concepts"):
            for entry in extracted.get(category, []):
                self._add(category, entry["name"], entry["description"])

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

    def set_name_pools(self, pools: Dict[str, List[str]]):
        """Store name pools inside the glossary data."""
        self.glossary["_name_pools"] = pools
        self.save_glossary()

    def get_name_pools(self) -> Dict[str, List[str]]:
        """Retrieve stored name pools."""
        return self.glossary.get("_name_pools", {})

    def get_random_name(self, category: str, fallback_category: str = "any_character") -> Optional[str]:
        """Pick a random name from the given category pool."""
        pools = self.get_name_pools()
        pool = pools.get(category)
        if pool:
            return random.choice(pool)
        fallback = pools.get(fallback_category)
        if fallback:
            return random.choice(fallback)
        return None

    def assign_name_to_character(self, character_name: str, category: str = "protagonist") -> Optional[str]:
        """
        If the character doesn't already have a name, assign a random name from the pool.
        Returns the assigned name (or the existing one).
        """
        # character_name is a placeholder like "[PROTAGONIST]"
        if character_name not in self.glossary["characters"]:
            new_name = self.get_random_name(category)
            if new_name:
                self.glossary["characters"][new_name] = {
                    "name": new_name,
                    "description": f"Originally described as {character_name}",
                    "category": category,
                    "assigned": True
                }
                self.save_glossary()
                return new_name
        return character_name
