"""
AI Book Creator - A modular system for generating complete books using AI
"""

import os
import sys
from pathlib import Path

# The shared AI suite: the sibling ai-suite checkout when present, else the copy vendored
# into this repository (ai-suite's sync.py keeps it identical).
_SUITE = Path(os.getenv("AI_SUITE_DIR") or Path(__file__).resolve().parents[2] / "ai-suite")
if _SUITE.is_dir() and str(_SUITE) not in sys.path:
    sys.path.insert(0, str(_SUITE))

from ai_suite.env import load_local_env  # noqa: E402

load_local_env(Path(__file__).resolve().parent.parent / ".env")

from .core.book_creator import AIBookCreator
from .core.project_manager import ProjectManager
from .services.ai_service import AIService
from .utils.glossary_manager import GlossaryManager

__version__ = "2.0.0"
__all__ = [
    "AIBookCreator",
    "ProjectManager",
    "AIService",
    "GlossaryManager",
]
