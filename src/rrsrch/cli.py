"""rrsrch command line.

  rrsrch corpus discover PMID [PMID ...]   draft the corpus from meta-analyses
  rrsrch corpus fetch [--refresh]          snapshot every source and trial locally
  rrsrch corpus verify                     prove local snapshots match the lockfile
  rrsrch corpus status                     what the corpus holds, and what needs review
  rrsrch ledger load                       migrate Postgres and load the corpus
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from .corpus import discover, manifest, snapshot, text
from .sources.ctgov import ClinicalTrials, find_nct_ids
from .sources.europepmc import EuropePMC

# Fallback query when a Table 1 citation isn't in the reference list Europe PMC
# resolved. Any hit is marked for review: a search is a likely match, not a proof.
FALLBACK_QUERY = ('AUTH:"{surname}" AND PUB_YEAR:{year} AND (knee OR osteoarthritis) '
                  'AND (mesenchymal OR stromal OR "stem cell" OR "stromal vascular")')
# Second fallback: the cited title. Only a record whose whole title matches it,
# ignoring case and punctuation, counts; preprints yield to the journal version.
TITLE_QUERY = 'TITLE:"{phrase}"'


def _norm_title(t: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", t.lower()).split())


def _search_title(epmc: EuropePMC, title: str) -> dict | None:
    want = _norm_title(title)
    phrase = " ".join(want.split()[:12])
    hits = [h for h in epmc.search(TITLE_QUERY.format(phrase=phrase), page_size=10)
            if _norm_title(h.get("title") or "") == want]
    if len(hits) > 1:
        hits = [h for h in hits if h.get("source") != "PPR"]
    return {"id": f'{hits[0]["source"]}:{hits[0]["id"]}', "title": hits[0]["title"]} if len(hits) == 1 else None


def _search_one(epmc: EuropePMC, query: str) -> dict | None:
    hits = [h for h in epmc.search(query, page_size=5)
            if not discover.REVIEW_RE.search(h.get("title") or "")]
    return {"id": f'{hits[0]["source"]}:{hits[0]["id"]}', "title": hits[0]["title"]} if len(hits) == 1 else None


def cmd_discover(args) -> int:
    epmc = EuropePMC()
    data = manifest.load()
    if args.topic:
        data["topic"] = args.topic
    for pmid in args.pmids:
        rec = epmc.record(f"MED:{pmid}")
        if not rec or not rec.get("pmcid"):
            print(f"{pmid}: no open full text, so no Table 1 to read; skipped", file=sys.stderr)
            continue
        xml = epmc.full_text_xml(rec["pmcid"])
        if xml is None:
            print(f"{pmid}: Europe PMC has no full-text XML; skipped", file=sys.stderr)
            continue
        citations = discover.table1_citations(xml)
        resolutions = discover.resolve(citations, epmc.references(pmid))
        searched = {}
        for i, r in enumerate(resolutions):
            if r.status not in ("unresolved", "ambiguous"):
                continue
            hit = None
            if r.citation.year:
                hit = _search_one(epmc, FALLBACK_QUERY.format(surname=r.citation.surname,
                                                              year=r.citation.year))
            title = discover.cited_title(r.citation.cited_as)
            if hit is None and len(title.split()) >= 5:
                hit = _search_title(epmc, title)
            if hit:
                searched[i] = hit
        ma = {"pmid": pmid, "pmcid": rec["pmcid"], "title": rec["title"],
              "year": int(rec["pubYear"]), "included_studies_from": "table-1"}
        manifest.merge_meta_analysis(data, ma, resolutions, searched)
        counts = {}
        for i, r in enumerate(resolutions):
            k = "found-by-search (review)" if i in searched else r.status
            counts[k] = counts.get(k, 0) + 1
        print(f"{pmid}: {len(citations)} included studies cited in Table 1 -> {counts}")
    manifest.save(data)
    return cmd_status(args)


def cmd_fetch(args) -> int:
    epmc, ct = EuropePMC(), ClinicalTrials()
    data = manifest.load()
    lock = snapshot.Lock()
    changes = []
    ids = [s["id"] for s in data["studies"]] + [f'MED:{m["pmid"]}' for m in data["answer_key"]]
    study_ids = {s["id"] for s in data["studies"]}
    ncts: dict[str, str] = {}
    for sid in ids:
        d = snapshot.record_dir(sid)
        if (d / "record.json").exists() and not args.refresh:
            rec = json.loads((d / "record.json").read_text())
        else:
            rec = epmc.record(sid)
            if rec is None:
                print(f"{sid}: not found in Europe PMC", file=sys.stderr)
                continue
            changes.append(lock.write(d / "record.json", snapshot.stable_record_bytes(rec),
                                      f"europepmc:{sid}"))
        ft = d / "fulltext.xml"
        if rec.get("pmcid") and rec.get("isOpenAccess") == "Y" and (args.refresh or not ft.exists()):
            xml = epmc.full_text_xml(rec["pmcid"])
            if xml is not None:
                changes.append(lock.write(ft, xml, f"europepmc:{rec['pmcid']}/fullTextXML"))
        if sid in study_ids:
            body = text.abstract_text(rec.get("abstractText"))
            if ft.exists():
                body += "\n" + text.fulltext_body(ft.read_bytes())
            for n in find_nct_ids(body):
                ncts.setdefault(n, sid)
    for nct in sorted(ncts):
        p = snapshot.trial_path(nct)
        if p.exists() and not args.refresh:
            continue
        raw = ct.study(nct)
        if raw is None:
            print(f"{nct} (cited by {ncts[nct]}): not on ClinicalTrials.gov", file=sys.stderr)
            continue
        changes.append(lock.write(p, raw, f"ctgov:{nct}"))
    lock.save()
    summary = {}
    for c in changes:
        summary[c.kind] = summary.get(c.kind, 0) + 1
    print(f"snapshots: {summary or 'nothing new'}; trials referenced: {len(ncts)}")
    for c in changes:
        if c.kind == "changed":
            print(f"  changed at source: {c.path}")
    return 0


def cmd_verify(args) -> int:
    problems = snapshot.Lock().verify()
    for path, why in problems:
        print(f"{why}: {path}")
    print("all snapshots match the lockfile" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_status(args) -> int:
    data = manifest.load()
    studies = data["studies"]
    print(f"answer key: {len(data['answer_key'])} meta-analyses; corpus: {len(studies)} studies")
    review = [s for s in studies if s.get("review")]
    for s in review:
        print(f"  review: {s['id']}  cited as {list(s['cited_as'].values())[0]!r} -> {s['title'][:70]}")
    for u in data["unresolved"]:
        print(f"  unresolved: {u['cited_as']!r} in {u['meta_analysis']} ({u['status']})")
    return 0


def cmd_ledger_load(args) -> int:
    from .ledger import db
    with db.connect() as conn:
        applied = db.migrate(conn)
        if applied:
            print(f"migrations applied: {', '.join(applied)}")
        rep = db.load_corpus(conn, manifest.load())
    print(f"loaded {rep.sources} sources, documents {rep.documents}, "
          f"{rep.trials} trials, {rep.links} trial links")
    for m in rep.missing_snapshots:
        print(f"  no snapshot yet (run `rrsrch corpus fetch`): {m}")
    for n in rep.unregistered_ncts:
        print(f"  cited NCT with no ClinicalTrials.gov record: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rrsrch")
    sub = p.add_subparsers(dest="group", required=True)
    corpus = sub.add_parser("corpus").add_subparsers(dest="cmd", required=True)
    d = corpus.add_parser("discover", help="draft the corpus from meta-analyses (PubMed ids)")
    d.add_argument("pmids", nargs="+")
    d.add_argument("--topic")
    d.set_defaults(fn=cmd_discover)
    f = corpus.add_parser("fetch", help="snapshot every source and trial locally")
    f.add_argument("--refresh", action="store_true", help="re-fetch and report what changed at the source")
    f.set_defaults(fn=cmd_fetch)
    corpus.add_parser("verify").set_defaults(fn=cmd_verify)
    corpus.add_parser("status").set_defaults(fn=cmd_status)
    ledger = sub.add_parser("ledger").add_subparsers(dest="cmd", required=True)
    ledger.add_parser("load").set_defaults(fn=cmd_ledger_load)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
