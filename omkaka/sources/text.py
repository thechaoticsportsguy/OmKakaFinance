"""Cleaning outside text.

News articles, filings, and Reddit posts are UNTRUSTED EVIDENCE, never
instructions. We strip HTML/control characters, cap the length, and store the
text only as data. Nothing in this app ever executes or obeys stored text.
"""
from __future__ import annotations

import hashlib
import html
import re

_TAG = re.compile(r"<[^>]+>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def clean_text(text: str | None, max_chars: int = 1000) -> str | None:
    if not text:
        return None
    text = html.unescape(_TAG.sub(" ", text))
    text = _CONTROL.sub("", text)
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text or None


def story_group(title: str | None, url: str | None = None) -> str:
    """Same normalized headline = same story, even if republished elsewhere."""
    basis = _NON_WORD.sub(" ", (title or "").lower()).strip() or (url or "")
    return hashlib.sha256(basis.encode()).hexdigest()[:16]
