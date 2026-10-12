"""The exact text a claim span points into.

Claims cite character offsets, so turning a snapshot into text must be
deterministic: the same snapshot always yields the same string. Nothing here
calls a model.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def _clean(s: str) -> str:
    return _WS.sub(" ", s).strip()


def abstract_text(abstract_html: str | None) -> str:
    """Europe PMC abstracts carry light HTML (<h4>Methods</h4>, <b>). Headings become their own lines."""
    if not abstract_html:
        return ""
    s = re.sub(r"</?(h\d|p|br)[^>]*>", "\n", abstract_html, flags=re.I)
    s = html.unescape(_TAG.sub("", s))
    return "\n".join(line for line in (_clean(x) for x in s.split("\n")) if line)


def fulltext_body(jats_xml: bytes) -> str:
    """Body text of a JATS article: section titles and paragraphs, one per block, in reading order.

    Tables, figures and references are left out; they get their own handling
    when the reader needs them.
    """
    root = ET.fromstring(jats_xml)
    body = root.find(".//body")
    if body is None:
        return ""
    blocks: list[str] = []

    def walk(el: ET.Element) -> None:
        if el.tag in ("table-wrap", "fig", "ref-list", "supplementary-material"):
            return
        if el.tag in ("title", "p"):
            t = _clean("".join(el.itertext()))
            if t:
                blocks.append(t)
            return
        for child in el:
            walk(child)

    walk(body)
    return "\n\n".join(blocks)
