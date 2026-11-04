"""Bold / italic for SMS, which has no markup (owner request 2026-11-04).

Letters and digits map to Unicode Mathematical Sans-Serif Bold / Italic; everything else passes through.
Any styled character (or emoji) switches the whole SMS to UCS-2: 67 characters per segment instead of 153.
Never style a reply code: a copied styled digit is not an ASCII digit and would not parse.
"""
from __future__ import annotations


def _table(upper: int, lower: int, digit: int | None) -> dict[int, str]:
    t = {ord("A") + i: chr(upper + i) for i in range(26)} | {ord("a") + i: chr(lower + i) for i in range(26)}
    if digit is not None:
        t |= {ord("0") + i: chr(digit + i) for i in range(10)}
    return t


_BOLD = _table(0x1D5D4, 0x1D5EE, 0x1D7EC)
_ITALIC = _table(0x1D608, 0x1D622, None)            # no italic digits in Unicode; they stay plain


def bold(s: str) -> str:
    return s.translate(_BOLD)


def italic(s: str) -> str:
    return s.translate(_ITALIC)
