"""
Utility functions for text processing, such as word and page counting.
"""

WORDS_PER_PAGE = 250


def calculate_word_count(text: str) -> int:
    """Calculates the number of words in a given text."""
    return len(text.split())


def calculate_page_count(word_count: int, words_per_page: int = WORDS_PER_PAGE) -> int:
    """Calculates the estimated number of pages based on word count (rounded up)."""
    return (word_count + words_per_page - 1) // words_per_page


# --- NEW: canonical conversions ---
def pages_to_words(pages: int) -> int:
    return pages * WORDS_PER_PAGE


def words_to_pages(words: int) -> float:
    return words / WORDS_PER_PAGE


# --- NEW: sane token estimation ---
def estimate_tokens_from_words(words: int) -> int:
    return int(words * 1.3)