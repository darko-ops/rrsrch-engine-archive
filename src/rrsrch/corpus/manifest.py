"""corpus/manifest.yaml: the fixed v1 corpus, as reviewed by a person.

`rrsrch corpus discover` drafts it from meta-analyses; a person checks every
entry marked `review: true` and every `unresolved` one. Discover never touches
an entry a person resolved by hand (`resolved_by: manual`).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .discover import Resolution

DEFAULT_PATH = Path("corpus/manifest.yaml")


def load(path: Path = DEFAULT_PATH) -> dict:
    if not path.exists():
        return {"topic": "", "answer_key": [], "studies": [], "unresolved": []}
    data = yaml.safe_load(path.read_text()) or {}
    for k in ("answer_key", "studies", "unresolved"):
        data.setdefault(k, [])
    return data


def save(data: dict, path: Path = DEFAULT_PATH) -> None:
    data["answer_key"].sort(key=lambda m: m["pmid"])
    data["studies"].sort(key=lambda s: s["id"])
    data["unresolved"].sort(key=lambda u: (u["meta_analysis"], u["cited_as"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))


def merge_meta_analysis(data: dict, ma: dict, resolutions: list[Resolution],
                        searched: dict[int, dict]) -> None:
    """Fold one meta-analysis's included studies into the manifest.

    `searched` maps the index of a resolution that failed against the
    reference list to a record found by Europe PMC search instead. Those are
    added with `review: true`: a search hit is a likely match, not a proven one.
    """
    pmid = ma["pmid"]
    data["answer_key"] = [m for m in data["answer_key"] if m["pmid"] != pmid] + [ma]
    studies = {s["id"]: s for s in data["studies"]}
    answer_key_ids = {f'MED:{m["pmid"]}' for m in data["answer_key"]}
    unresolved = [u for u in data["unresolved"] if u["meta_analysis"] != pmid]

    for i, r in enumerate(resolutions):
        cited = r.citation.cited_as
        if r.status == "resolved":
            sid, title, how, review = r.source_id, r.title, "reference-list", False
        elif i in searched:
            hit = searched[i]
            sid, title, how, review = hit["id"], hit["title"], "europepmc-search", True
        else:
            claimed = any(s.get("cited_as", {}).get(pmid) == cited for s in studies.values())
            if not claimed:
                unresolved.append({"meta_analysis": pmid, "cited_as": cited, "status": r.status,
                                   "candidates": [c["id"] for c in r.candidates]})
            continue
        if sid in answer_key_ids:
            continue                      # a meta-analysis is the answer key, never evidence
        s = studies.setdefault(sid, {"id": sid, "title": title, "included_by": [],
                                     "resolved_by": how, "review": review, "cited_as": {}})
        if pmid not in s["included_by"]:
            s["included_by"] = sorted(s["included_by"] + [pmid])
        s.setdefault("cited_as", {})[pmid] = cited
        if s["resolved_by"] != "manual" and how == "reference-list":
            s["resolved_by"], s["review"] = how, False     # a proven match supersedes a search hit

    data["studies"] = list(studies.values())
    data["unresolved"] = unresolved
