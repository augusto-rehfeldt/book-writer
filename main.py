#!/usr/bin/env python3
"""Compatibility wrapper for the package CLI."""

# ponytail: load_local_env() runs once in ai_book_creator/__init__.py on import.
from ai_book_creator.cli import main


if __name__ == "__main__":
    main()
