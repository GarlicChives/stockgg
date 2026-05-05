"""Prompt registry — load .md prompt files alongside this module.

Why files instead of constants:
  - Edit a prompt without touching code (no Python re-deploy needed for tweaks)
  - Single source of truth referenced by PROMPTS.md catalog

Substitution syntax: Python `string.Template` with `$var` placeholders.
Chosen over f-strings / .format() because prompt bodies contain literal `{}`
(JSON examples) which would otherwise need escaping.
"""
from pathlib import Path
from string import Template

_DIR = Path(__file__).resolve().parent


def load(name: str) -> str:
    """Return raw prompt body from src/prompts/{name}.md (no substitution)."""
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def render(name: str, **kwargs: object) -> str:
    """Load + Template.substitute. Raises KeyError on missing $var."""
    return Template(load(name)).substitute(**kwargs)
