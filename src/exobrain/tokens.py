"""Conservative token estimate. Exact counts differ per AI, so we over-estimate:
one token per CJK/kana character, one per four other characters."""

from __future__ import annotations


def _is_wide(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3000 <= o <= 0x30FF  # CJK punctuation, hiragana, katakana
        or 0x3400 <= o <= 0x9FFF  # CJK ideographs
        or 0xF900 <= o <= 0xFAFF
        or 0xFF00 <= o <= 0xFFEF  # full-width forms
    )


def estimate_tokens(text: str) -> int:
    wide = sum(1 for ch in text if _is_wide(ch))
    narrow = len(text) - wide
    return wide + (narrow + 3) // 4
