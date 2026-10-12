"""ClinicalTrials.gov (API v2): what a trial team registered before reporting.

Registries outside ClinicalTrials.gov (ChiCTR, IRCT, ISRCTN, the EU register)
are not searched yet, and reports must say so.
"""
from __future__ import annotations

import re

from .http import Http

BASE = "https://clinicaltrials.gov/api/v2/studies"

NCT_RE = re.compile(r"\bNCT\s?(\d{8})\b")


def find_nct_ids(text: str) -> list[str]:
    """NCT numbers mentioned in text, normalized ("NCT 01234567" -> "NCT01234567"), in order, deduplicated."""
    return list(dict.fromkeys(f"NCT{d}" for d in NCT_RE.findall(text or "")))


class ClinicalTrials:
    def __init__(self, http: Http | None = None):
        self.http = http or Http()

    def study(self, nct_id: str) -> bytes | None:
        """The raw registration record; None if ClinicalTrials.gov has no such trial."""
        r = self.http.get(f"{BASE}/{nct_id}", {"format": "json"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content
