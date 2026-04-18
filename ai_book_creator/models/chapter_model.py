from typing import List, Optional

try:
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - fallback for minimal environments
    class BaseModel:
        def __init__(self, **data):
            for key, value in data.items():
                setattr(self, key, value)

    def Field(default=None, **kwargs):
        return default


class Scene(BaseModel):
    title: str = Field(description="Brief title or summary of the scene.")
    setting: str = Field(description="Detailed description of the scene's location and environment, including sensory details.")
    characters_present: List[str] = Field(description="List of main characters present in this scene.")
    plot_advancement: str = Field(description="How this scene moves the overall plot forward.")
    character_development: Optional[str] = Field(description="Key moments of character development or revelation in this scene.")
    dialogue_summary: Optional[str] = Field(description="Summary of key dialogue or conversation topics.")
    emotional_beat: Optional[str] = Field(description="The primary emotional tone or impact of the scene.")
    pacing_notes: Optional[str] = Field(description="Notes on the pacing of the scene (e.g., fast, slow, suspenseful).")

class ChapterContent(BaseModel):
    chapter_title: str = Field(description="The title of the chapter.")
    chapter_summary: str = Field(description="A concise summary of the chapter's main events and themes.")
    scenes: List[Scene] = Field(description="A list of detailed scenes that make up the chapter.")
    overall_pacing: str = Field(description="Overall pacing of the chapter (e.g., building tension, reflective, action-packed).")
    key_themes: List[str] = Field(description="List of key themes explored in this chapter.")
    word_count_estimate: int = Field(description="Estimated word count for the full chapter.")

class Chapter:
    def __init__(self, title: str, content: str, chapter_number: int, word_count: int, filename: Optional[str] = None):
        self.title = title
        self.content = content
        self.chapter_number = chapter_number
        self.word_count = word_count
        self.filename = filename

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "chapter_number": self.chapter_number,
            "word_count": self.word_count,
            "filename": self.filename
        }