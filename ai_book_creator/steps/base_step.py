"""
Base Step class - defines the interface for all book creation steps
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseStep(ABC):
    def __init__(self, ai_service, project_manager, glossary_manager=None):
        self.ai_service = ai_service
        self.project_manager = project_manager
        self.glossary_manager = glossary_manager
        self.step_name = self.__class__.__name__.lower().replace('step', '')
    
    @abstractmethod
    def execute(self) -> Dict[str, Any]:
        """Execute the step and return the result"""
        pass
    
    @abstractmethod
    def should_execute(self) -> bool:
        """Check if this step needs to be executed"""
        pass
    
    @abstractmethod
    def get_step_header(self) -> str:
        """Get the formatted header for this step"""
        pass
    
    def get_step_data(self) -> Dict[str, Any]:
        """Get existing data for this step"""
        return self.project_manager.get_step_data(self.step_name)
    
    def save_step_data(self, data: Dict[str, Any]):
        """Save data for this step"""
        self.project_manager.set_step_data(self.step_name, data)
    
    def mark_completed(self, completion_key: str = "completed"):
        """Mark this step as completed"""
        self.project_manager.mark_step_completed(self.step_name, completion_key)
    
    def is_completed(self, completion_key: str = "completed") -> bool:
        """Check if this step is completed"""
        return self.project_manager.is_step_completed(self.step_name, completion_key)
