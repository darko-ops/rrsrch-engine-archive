"""Find the studies a meta-analysis included, from its own Table 1.

A meta-analysis's reference list cites everything (methods papers, background,
other reviews), so it can't stand in for the included studies. Table 1, "the
characteristics of included studies", can. Its rows cite trials in one of two
ways, and both are handled:

  - by cross-reference to the bibliography (<xref rid="CR27">), or
  - by "Author et al." plus a year column, with no link at all.

Each row is resolved against Europe PMC's copy of the reference list, which
carries a PubMed id for nearly every entry even when the article's own XML
doesn't. Matching is deterministic: normalized first-author surname + year,
and a match only counts when it is unique. Anything else is reported as
ambiguous or unresolved for a person to check, never guessed.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

YEAR_RE = re.compile(r"\b(?:19|20)\d\d\b")
REVIEW_RE = re.compile(r"meta-?analys|systematic review|umbrella review", re.I)


def _text(el: ET.Element | None) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def norm_surname(s: str) -> str:
    """'Martín-Ferrero' -> 'martinferrero'; 'Lamo-Espinoza' and 'Lamo Espinosa' both start 'lamoespino'."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


@dataclass
class Citation:
    """How the meta-analysis cites one included study."""
    cited_as: str            # what a person would recognize: the table cell or reference text
    surname: str
    year: str


def cited_title(cited_as: str) -> str:
    """The title part of a reference string: what follows "et al." or the author list."""
    m = re.search(r"et al\.?,?\s+(.+)", cited_as)
    rest = m.group(1) if m else cited_as.split(". ", 1)[-1]
    return rest.split(". ")[0].strip(" \u201c\u201d\"'")


@dataclass
class Resolution:
    citation: Citation
    status: str              # resolved | ambiguous | unresolved | excluded_review
    source_id: str | None = None
    title: str = ""
    candidates: list[dict] = field(default_factory=list)


def _ref_citation(ref: ET.Element) -> Citation:
    raw = _text(ref)
    body = re.sub(r"^\[?\d+\]?\.?\s*", "", raw)        # drop a leading "27." / "[27]"
    names = [n.findtext("surname") for n in ref.iter("name")]
    surname = names[0] if names and names[0] else re.split(r"[ ,.]", body)[0]
    years = [y.text for y in ref.iter("year") if y.text] or YEAR_RE.findall(body)
    return Citation(cited_as=body[:400], surname=surname, year=(years[0] if years else "")[:4])


def table1_citations(xml: bytes) -> list[Citation]:
    """The included studies, as Table 1 cites them, in table order."""
    root = ET.fromstring(xml)
    refs = {r.get("id"): r for r in root.iter("ref")}
    table = next((t for t in root.iter("table-wrap")
                  if re.match(r"Table\s*1\b", _text(t.find("label")))), None)
    if table is None:
        raise ValueError("no Table 1 in this article")

    out: list[Citation] = []
    seen: set[str] = set()
    for tr in table.iter("tr"):
        cells = [_text(c) for c in tr if c.tag == "td"]
        if not cells or not cells[0]:
            continue
        rids = [rid for x in tr.iter("xref") if x.get("ref-type") == "bibr"
                for rid in (x.get("rid") or "").split()]
        if rids:
            for rid in rids:
                if rid in refs and rid not in seen:
                    seen.add(rid)
                    out.append(_ref_citation(refs[rid]))
        else:
            m = re.search(r"[A-Z][A-Za-z'À-ſ\-]+", cells[0])
            year = next((y for c in cells[:3] for y in YEAR_RE.findall(c)), "")
            key = f"{cells[0]}|{year}"
            if m and key not in seen:
                seen.add(key)
                out.append(Citation(cited_as=f"{cells[0]} ({year})" if year else cells[0],
                                    surname=m.group(0), year=year))
    return out


def resolve(citations: list[Citation], epmc_refs: list[dict]) -> list[Resolution]:
    """Match each citation to exactly one entry in Europe PMC's reference list."""
    out = []
    for c in citations:
        s = norm_surname(c.surname)
        # prefix match tolerates spelling drift inside a surname ("Lamo-Espinoza")
        # while staying anchored to its start; a short surname must match whole.
        key = s[:8] if len(s) > 8 else s
        hits = [e for e in epmc_refs if e.get("id")
                and norm_surname((e.get("authorString") or "").split(" ")[0]).startswith(key)
                and (not c.year or str(e.get("pubYear")) == c.year)]
        primary = [h for h in hits if not REVIEW_RE.search(h.get("title") or "")]
        cands = [{"id": f'{h.get("source")}:{h.get("id")}', "title": h.get("title"),
                  "year": h.get("pubYear")} for h in hits]
        if len(primary) == 1:
            h = primary[0]
            out.append(Resolution(c, "resolved", f'{h.get("source")}:{h.get("id")}',
                                  h.get("title") or "", cands))
        elif hits and not primary:
            out.append(Resolution(c, "excluded_review", candidates=cands))
        elif primary:
            out.append(Resolution(c, "ambiguous", candidates=cands))
        else:
            out.append(Resolution(c, "unresolved"))
    return out
