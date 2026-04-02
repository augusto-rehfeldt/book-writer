#!/usr/bin/env python3
"""Compatibility wrapper for the package CLI."""

from ai_book_creator.env import load_local_env

load_local_env()

from ai_book_creator.cli import main


if __name__ == "__main__":
    main()
