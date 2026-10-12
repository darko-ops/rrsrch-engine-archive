"""Find a model's quote in the document, exactly, or reject it.

The reader asks the model to copy each claim's supporting passage verbatim.
Models drift in typography (curly vs straight quotes, dash widths, spacing), so
those are normalized on both sides. Nothing else is: a quote with a changed
word, number or letter case is not in the document, and the claim is rejected.
That is what stops a model from inventing a passage to cite.
"""
from __future__ import annotations

_TRANSLATE = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
}


def _normalize(text: str) -> tuple[str, list[int]]:
    """Normalized text plus, for each of its characters, the offset it came from."""
    out: list[str] = []
    origin: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        ch = _TRANSLATE.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch)
        origin.append(i)
    return "".join(out), origin


def locate(quote: str, text: str, start: int = 0, end: int | None = None) -> tuple[int, int] | None:
    """(start, end) offsets of quote in text[start:end], or None if it isn't there verbatim."""
    q, _ = _normalize(quote.strip())
    if len(q) < 12:
        return None                          # too short to pin a claim to
    window = text[start:end]
    t, origin = _normalize(window)
    pos = t.find(q)
    if pos < 0:
        return None
    return start + origin[pos], start + origin[pos + len(q) - 1] + 1
