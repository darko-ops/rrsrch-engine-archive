from rrsrch.corpus import manifest
from rrsrch.corpus.discover import Citation, Resolution


def empty():
    return {"topic": "t", "answer_key": [], "studies": [], "unresolved": []}


def ma(pmid):
    return {"pmid": pmid, "pmcid": f"PMC{pmid}", "title": f"MA {pmid}", "year": 2025,
            "included_studies_from": "table-1"}


def res(cited, status, sid=None):
    return Resolution(Citation(cited, cited.split()[0], "2020"), status, sid, f"title of {sid}")


def test_search_hits_need_review_and_a_proven_match_clears_it():
    d = empty()
    manifest.merge_meta_analysis(d, ma("1"), [res("Gupta 2020", "unresolved")],
                                 {0: {"id": "MED:50", "title": "Gupta trial"}})
    [s] = d["studies"]
    assert (s["resolved_by"], s["review"]) == ("europepmc-search", True)
    manifest.merge_meta_analysis(d, ma("2"), [res("Gupta 2020", "resolved", "MED:50")], {})
    [s] = d["studies"]
    assert (s["resolved_by"], s["review"], s["included_by"]) == ("reference-list", False, ["1", "2"])


def test_a_meta_analysis_is_never_added_as_evidence():
    d = empty()
    manifest.merge_meta_analysis(d, ma("1"), [], {})
    manifest.merge_meta_analysis(d, ma("2"), [res("Review 2021", "resolved", "MED:1")], {})
    assert d["studies"] == []


def test_manual_resolutions_survive_a_rerun():
    d = empty()
    d["studies"] = [{"id": "MED:7", "title": "x", "included_by": ["1"], "resolved_by": "manual",
                     "review": False, "cited_as": {"1": "Schweich 2022"}}]
    manifest.merge_meta_analysis(d, ma("1"), [res("Schweich 2022", "unresolved")], {})
    assert d["unresolved"] == []                      # claimed by the manual entry
    assert d["studies"][0]["resolved_by"] == "manual"


def test_rerunning_replaces_that_meta_analysis_unresolved_list_only():
    d = empty()
    manifest.merge_meta_analysis(d, ma("1"), [res("Ha 2018", "unresolved")], {})
    manifest.merge_meta_analysis(d, ma("2"), [res("Ho 2019", "unresolved")], {})
    manifest.merge_meta_analysis(d, ma("1"), [res("Ha 2018", "unresolved")], {})
    assert sorted(u["cited_as"] for u in d["unresolved"]) == ["Ha 2018", "Ho 2019"]
