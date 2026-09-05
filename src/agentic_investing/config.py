"""Application configuration and repository path resolution."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PACKAGE_ROOT / "docs" / "prompts"


def prompt_path(name: str) -> Path:
    """Return a validated path to a versioned Markdown prompt."""

    if not name or Path(name).name != name or not name.endswith(".md"):
        raise ValueError("prompt name must be a single Markdown filename")
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found: {path}")
    return path


def load_prompt(name: str) -> str:
    """Load a UTF-8 Markdown prompt from the repository prompt catalog."""

    return prompt_path(name).read_text(encoding="utf-8").strip()


def log_level() -> str:
    """Return the configured log level, defaulting to INFO."""

    return os.environ.get("AGENTIC_INVESTING_LOG_LEVEL", "INFO").upper()
