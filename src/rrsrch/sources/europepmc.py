"""Europe PMC: abstracts for nearly everything, full text for the open-access subset.

Records are identified Europe PMC's way, "SRC:ID" (MED:30762487 for a PubMed
record, PMC:PMC7273783 for a PMC-only one), so one id scheme covers both.
"""
from __future__ import annotations

from .http import Http

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def split_id(source_id: str) -> tuple[str, str]:
    src, _, ext = source_id.partition(":")
    if not ext:
        raise ValueError(f"expected SRC:ID, got {source_id!r}")
    return src, ext


class EuropePMC:
    def __init__(self, http: Http | None = None):
        self.http = http or Http()

    def search(self, query: str, page_size: int = 25, result_type: str = "lite") -> list[dict]:
        r = self.http.get(f"{BASE}/search", {"query": query, "format": "json",
                                             "pageSize": page_size, "resultType": result_type})
        r.raise_for_status()
        return r.json().get("resultList", {}).get("result", [])

    def record(self, source_id: str) -> dict | None:
        """Full ("core") metadata for one record, including the abstract."""
        src, ext = split_id(source_id)
        hits = self.search(f"EXT_ID:{ext} AND SRC:{src}", page_size=1, result_type="core")
        return hits[0] if hits else None

    def references(self, pmid: str) -> list[dict]:
        """A paper's reference list, with Europe PMC's own id for each entry it could match."""
        r = self.http.get(f"{BASE}/MED/{pmid}/references", {"format": "json", "pageSize": 1000})
        r.raise_for_status()
        return r.json().get("referenceList", {}).get("reference", [])

    def full_text_xml(self, pmcid: str) -> bytes | None:
        """JATS XML for an open-access article; None when there is no open full text."""
        r = self.http.get(f"{BASE}/{pmcid}/fullTextXML")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content
