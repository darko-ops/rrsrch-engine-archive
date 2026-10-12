"""Local snapshots of everything rrsrch reads, pinned by hash.

Every fetched record is written under data/snapshots/ and its sha256 recorded
in corpus/snapshots.lock.json, which is committed. The snapshots themselves are
not (article text carries per-paper licenses), but anyone can re-fetch them
and `rrsrch corpus verify` proves they got the same bytes. If a paper is later
updated or retracted at the source, a refresh shows exactly which file changed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_ROOT = Path("data/snapshots")
LOCK_PATH = Path("corpus/snapshots.lock.json")

# Fields Europe PMC changes without the paper changing. Stored snapshots leave
# them out so a citation count ticking up never looks like an edited paper.
VOLATILE_RECORD_FIELDS = {"citedByCount"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_record_bytes(record: dict) -> bytes:
    kept = {k: v for k, v in record.items() if k not in VOLATILE_RECORD_FIELDS}
    return json.dumps(kept, sort_keys=True, ensure_ascii=False, indent=1).encode()


def record_dir(source_id: str) -> Path:
    return SNAPSHOT_ROOT / "europepmc" / source_id.replace(":", "_")


def trial_path(nct_id: str) -> Path:
    return SNAPSHOT_ROOT / "ctgov" / f"{nct_id}.json"


@dataclass
class Change:
    path: str
    kind: str        # new | changed | unchanged


class Lock:
    def __init__(self, path: Path = LOCK_PATH):
        self.path = path
        self.entries: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}

    def write(self, file: Path, data: bytes, url: str) -> Change:
        """Store a snapshot and pin its hash. Reports whether the content is new or changed."""
        key = file.as_posix()
        digest = sha256(data)
        prior = self.entries.get(key)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        if prior and prior["sha256"] == digest:
            return Change(key, "unchanged")
        self.entries[key] = {"sha256": digest, "bytes": len(data), "url": url,
                             "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        return Change(key, "changed" if prior else "new")

    def verify(self) -> list[tuple[str, str]]:
        """Every pinned file must exist with its pinned hash. Returns (path, problem) pairs."""
        problems = []
        for key, entry in sorted(self.entries.items()):
            p = Path(key)
            if not p.exists():
                problems.append((key, "missing"))
            elif sha256(p.read_bytes()) != entry["sha256"]:
                problems.append((key, "hash mismatch"))
        return problems

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(dict(sorted(self.entries.items())), indent=1) + "\n")
