"""
AI Book Creator - A modular system for generating complete books using AI
"""

from .core.book_creator import AIBookCreator
from .core.project_manager import ProjectManager
from .services.ai_service import AIService
from .steps import *
from .utils.glossary_manager import GlossaryManager

__version__ = "2.0.0"
__all__ = [
    "AIBookCreator", 
    "ProjectManager", 
    "AIService",
    "GlossaryManager"
]
