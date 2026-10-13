from datetime import date

from rrsrch.trials import reconcile as R


def trial(primary, secondary=(), start="2015-01", first="2014-12-01", updated="2016-01-01",
          status="COMPLETED", completed="2017-06", pmid="1"):
    return {"protocolSection": {
        "statusModule": {"overallStatus": status, "startDateStruct": {"date": start},
                         "studyFirstSubmitDate": first, "lastUpdatePostDateStruct": {"date": updated},
                         "completionDateStruct": {"date": completed}},
        "outcomesModule": {"primaryOutcomes": [{"measure": m, "timeFrame": t} for m, t in primary],
                           "secondaryOutcomes": [{"measure": m, "timeFrame": t} for m, t in secondary]},
        "referencesModule": {"references": [{"pmid": pmid, "type": "RESULT"}]}}}


SOURCE = {"id": "MED:1", "pub_year": 2018, "pmid": "1", "trials": ["NCT00000001"]}


def docs(abstract, fulltext=""):
    out = [{"id": 1, "part": "abstract", "text": abstract}]
    if fulltext:
        out.append({"id": 2, "part": "fulltext", "text": fulltext})
    return out


def run(t, d, source=SOURCE):
    return R.reconcile_pair("NCT00000001", t, source, d)


def test_time_frames_in_registry_shapes():
    assert R.parse_times("Weeks 0, 24").points == {7 / 30.4375 * 24}
    assert R.parse_times("Week 0 and 48").covers(11)
    assert R.parse_times("1 to 5 years").spans == [(12.0, 60.0)]
    assert R.parse_times("up to 1 year").covers(6)
    assert R.parse_times("36 months, 48 months, and 60 months").points == {36.0, 48.0, 60.0}


def test_matched_when_the_declared_primary_is_the_registered_one():
    r = run(trial([("WOMAC score", "24 weeks")]),
            docs("Methods\nThe primary outcome was the WOMAC score at 6 months. Results\nWOMAC improved."))
    assert (r.state, r.flags) == ("matched", [])
    assert any(e.get("registry", "").endswith("primaryOutcomes[0]") for e in r.evidence)
    assert any("document_id" in e for e in r.evidence)


def test_a_secondary_outcome_promoted_to_primary_is_drift():
    r = run(trial([("WOMAC score", "12 months")], secondary=[("VAS pain", "12 months")]),
            docs("The primary outcome was VAS pain at 12 months; secondary outcomes included WOMAC."))
    assert r.state == "endpoint_drift"
    assert r.reasons == ["the paper's primary outcome vas_pain was registered as secondary (VAS pain)"]


def test_a_primary_reported_at_an_unregistered_time_is_drift():
    r = run(trial([("WOMAC score", "24 months")]), docs("The primary outcome was WOMAC at 6 months."))
    assert r.state == "endpoint_drift" and "reported at 6 months" in r.reasons[0]


def test_a_registered_primary_missing_from_the_full_text_is_drift_but_not_from_an_abstract():
    t = trial([("WOMAC score", "12 months"), ("Cartilage volume on MRI", "12 months")])
    abstract = "The primary outcome was WOMAC at 12 months."
    full = "Methods\n\nThe primary outcome was WOMAC at 12 months.\n\nResults\n\nWOMAC improved at 12 months."
    assert run(t, docs(abstract, full)).state == "endpoint_drift"
    assert run(t, docs(abstract)).state == "matched"          # absent from an abstract proves nothing


def test_a_paper_that_never_says_what_was_primary_cannot_be_matched():
    r = run(trial([("WOMAC score", "12 months")]), docs("WOMAC improved at 12 months (p = 0.01)."))
    assert r.state == "couldnt_match" and "does not say which outcome was primary" in r.reasons[0]


def test_a_heading_can_declare_the_primary_outcome():
    full = "Methods\n\nPrimary Outcome\n\nSafety was measured by adverse events. Others followed.\n\nResults\n\nNone."
    r = run(trial([("Adverse events", "12 months")]), docs("Ten patients.", full))
    assert r.state == "matched"


def test_a_paper_that_only_cites_the_trial_is_not_reconciled():
    t = trial([("WOMAC score", "12 months")], pmid="999")
    full = "Methods\n\nThe sample size was based on NCT00000001.\n\nThe primary outcome was WOMAC at 12 months."
    r = run(t, docs("Ten patients.", full))
    assert r.state == "couldnt_match" and "may only cite the trial" in r.reasons[0]
    full = "Methods\n\nOur trial was registered (NCT00000001).\n\nThe primary outcome was WOMAC at 12 months."
    assert run(t, docs("Ten patients.", full)).state == "matched"


def test_a_paper_reporting_two_trials_is_not_attributed():
    src = {**SOURCE, "trials": ["NCT00000001", "NCT00000002"]}
    r = run(trial([("WOMAC score", "12 months")]), docs("The primary outcome was WOMAC at 12 months."), src)
    assert r.state == "couldnt_match"


def test_a_general_primary_neither_confirms_nor_contradicts():
    r = run(trial([("WOMAC score", "12 months")]), docs("The primary outcomes were patient-reported outcomes."))
    assert r.state == "couldnt_match" and "only generally" in r.reasons[0]


def test_registration_timing_and_later_edits_are_flags_not_states():
    t = trial([("WOMAC score", "12 months")], start="2015-01", first="2015-06-01", updated="2021-03-01")
    r = run(t, docs("The primary outcome was WOMAC at 12 months."))
    assert r.state == "matched"
    assert r.flags == ["registered_after_start", "registry_edited_after_publication"]


def test_no_publication_found_only_under_the_strict_rule():
    old = trial([("WOMAC", "12 months")], completed="2020-01")
    assert R.no_publication("NCT1", old, date(2026, 10, 1)).state == "no_publication_found"
    assert R.no_publication("NCT1", old, date(2021, 6, 1)).state == "couldnt_match"      # 17 months on
    running = trial([("WOMAC", "12 months")], status="UNKNOWN", completed="2018-01")
    assert R.no_publication("NCT1", running, date(2026, 10, 1)).state == "couldnt_match"
