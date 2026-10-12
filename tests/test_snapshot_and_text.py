import json

from rrsrch.corpus import snapshot, text
from rrsrch.sources.ctgov import find_nct_ids


def test_lock_reports_new_unchanged_changed_and_verify_catches_tampering(tmp_path):
    lock = snapshot.Lock(tmp_path / "lock.json")
    f = tmp_path / "s" / "record.json"
    assert lock.write(f, b"v1", "u").kind == "new"
    assert lock.write(f, b"v1", "u").kind == "unchanged"
    assert lock.write(f, b"v2", "u").kind == "changed"
    lock.save()
    assert snapshot.Lock(tmp_path / "lock.json").verify() == []
    f.write_bytes(b"edited")
    assert snapshot.Lock(tmp_path / "lock.json").verify() == [(f.as_posix(), "hash mismatch")]
    f.unlink()
    assert snapshot.Lock(tmp_path / "lock.json").verify() == [(f.as_posix(), "missing")]


def test_a_citation_count_ticking_up_is_not_a_changed_paper():
    a = snapshot.stable_record_bytes({"title": "T", "citedByCount": 3})
    b = snapshot.stable_record_bytes({"citedByCount": 9, "title": "T"})
    assert a == b and "citedByCount" not in json.loads(a)


def test_abstract_headings_become_lines_and_entities_decode():
    s = text.abstract_text("<h4>Objective</h4>To test MSCs &amp; PRP.<h4>Results</h4>VAS fell, <b>p</b>&lt;0.05.")
    assert s == "Objective\nTo test MSCs & PRP.\nResults\nVAS fell, p<0.05."


def test_fulltext_keeps_titles_and_paragraphs_in_order_and_skips_tables():
    xml = b"""<article><body><sec><title>Methods</title><p>Forty  patients.</p>
    <table-wrap><table><tr><td>secret table</td></tr></table></table-wrap>
    <sec><title>Outcomes</title><p>WOMAC at 12 months.</p></sec></sec></body></article>"""
    assert text.fulltext_body(xml) == "Methods\n\nForty patients.\n\nOutcomes\n\nWOMAC at 12 months."


def test_same_snapshot_same_text():
    xml = b"<article><body><p>a</p><p>b</p></body></article>"
    assert text.fulltext_body(xml) == text.fulltext_body(xml)


def test_nct_ids_are_normalized_and_deduplicated():
    assert find_nct_ids("Registered as NCT 01234567 (NCT01234567); see NCT02345678.") \
        == ["NCT01234567", "NCT02345678"]
    assert find_nct_ids("NCT123 is too short") == []
