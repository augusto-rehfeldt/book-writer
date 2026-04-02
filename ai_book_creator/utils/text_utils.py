"""
Utility functions for text processing, such as word and page counting.
"""

def calculate_word_count(text: str) -> int:
    """Calculates the number of words in a given text."""
    return len(text.split())

def calculate_page_count(word_count: int, words_per_page: int = 250) -> int:
    """Calculates the estimated number of pages based on word count (rounded up)."""
    return (word_count + words_per_page - 1) // words_per_page
