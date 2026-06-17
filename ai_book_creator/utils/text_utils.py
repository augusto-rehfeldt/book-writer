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


def pages_to_words(pages: int) -> int:
    return pages * WORDS_PER_PAGE


if __name__ == "__main__":
    assert calculate_word_count("a b c") == 3
    assert calculate_page_count(250) == 1
    assert calculate_page_count(251) == 2
    assert pages_to_words(2) == 500
    print("ok")
