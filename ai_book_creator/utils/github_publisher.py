"""Free fallback publishing: a GitHub release carrying the EPUB and cover.

Books go out under the pen name only, so the repository must belong to an account
or organization other than the logged-in personal one (`AI_BOOK_GITHUB_REPO`,
OWNER/NAME). A release is public the moment it is created.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


class GithubPublishError(RuntimeError):
    pass


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise GithubPublishError(f"gh {args[0]} {args[1] if len(args) > 1 else ''}: "
                                 f"{(result.stderr or result.stdout).strip()[:300]}")
    return result.stdout.strip()


def target_repo(login: str, repo: str | None = None) -> str:
    """OWNER/NAME to publish to; refuses the personal account (real name)."""
    repo = (repo if repo is not None else os.getenv("AI_BOOK_GITHUB_REPO", "")).strip()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise GithubPublishError(
            "Set AI_BOOK_GITHUB_REPO=OWNER/NAME, owned by a pen-name account or organization.")
    if repo.split("/")[0].lower() == login.lower():
        raise GithubPublishError(
            f"{repo} belongs to the personal account {login}; books are published under the "
            "pen name only. Use a repository owned by a pen-name organization.")
    return repo


def release_notes(package: dict) -> str:
    return (f"**{package['title']}** by {package['author']}\n\n"
            f"{package.get('description', '').strip()}\n\n"
            f"---\nWritten with AI assistance: {package.get('ai_tools', {}).get('text', 'AI models')}. "
            "Free to read. Download the EPUB below.")


def publish_package(package_path: str | os.PathLike[str]) -> str:
    """Create (once) a release for a prepared KDP package; returns its URL."""
    package_file = Path(package_path)
    package = json.loads(package_file.read_text(encoding="utf-8"))
    if package.get("github_url"):
        return package["github_url"]
    epub = Path(package.get("manuscript_file", ""))
    if not epub.is_file():
        raise GithubPublishError(f"EPUB not found: {epub}")
    repo = target_repo(_gh("api", "user", "--jq", ".login"))
    try:
        _gh("repo", "view", repo, "--json", "name")
    except GithubPublishError:
        _gh("repo", "create", repo, "--public", "--description", f"Books by {package['author']}")
    tag = re.sub(r"[^a-z0-9]+", "-", package["title"].lower()).strip("-")[:60] or "book"
    assets = [str(epub)] + [c for c in [package.get("cover_file", "")] if c and Path(c).is_file()]
    url = _gh("release", "create", tag, *assets, "--repo", repo,
              "--title", f"{package['title']} — {package['author']}",
              "--notes", release_notes(package)).splitlines()[-1]
    package["github_url"] = url
    package_file.write_text(json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8")
    return url
