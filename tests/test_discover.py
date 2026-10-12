from rrsrch.corpus import discover

# Two ways a meta-analysis's Table 1 cites its trials, in the shapes seen in
# real JATS: rows linked to the bibliography, and bare "Author et al." rows.
LINKED = b"""<article><body>
<table-wrap id="T1"><label>Table 1</label><table><tbody>
<tr><td>Emadedin [<xref ref-type="bibr" rid="R27">27</xref>]</td><td>2018</td></tr>
<tr><td>Vega [<xref ref-type="bibr" rid="R30">30</xref>]</td><td>2015</td></tr>
<tr><td>Vega again [<xref ref-type="bibr" rid="R30">30</xref>]</td><td>2015</td></tr>
</tbody></table></table-wrap></body>
<back><ref-list>
<ref id="R27"><mixed-citation>27. Emadedin M, Labibzadeh N, et al. Intra-articular implantation of autologous bone marrow-derived MSCs. Cytotherapy. 2018;20:1238.</mixed-citation></ref>
<ref id="R30"><element-citation><person-group><name><surname>Vega</surname></name></person-group><article-title>Allogeneic BM-MSCs</article-title><year>2015</year></element-citation></ref>
</ref-list></back></article>"""

UNLINKED = b"""<article><body>
<table-wrap><label>Table 1</label><table><tbody>
<tr><td>Kim et al.</td><td>2023</td><td>Korea</td></tr>
<tr><td>Lamo-Espinosa et al.</td><td>2016</td><td>Spain</td></tr>
</tbody></table></table-wrap></body></article>"""


def test_linked_rows_resolve_through_the_bibliography_once_each():
    cites = discover.table1_citations(LINKED)
    assert [(c.surname, c.year) for c in cites] == [("Emadedin", "2018"), ("Vega", "2015")]
    assert cites[0].cited_as.startswith("Emadedin M")


def test_unlinked_rows_use_author_and_year_columns():
    cites = discover.table1_citations(UNLINKED)
    assert [(c.surname, c.year) for c in cites] == [("Kim", "2023"), ("Lamo-Espinosa", "2016")]


def ref(id, author, year, title="A trial", source="MED"):
    return {"id": id, "source": source, "authorString": author, "pubYear": year, "title": title}


def test_resolution_is_unique_or_it_is_not_a_match():
    refs = [ref("1", "Kim KI, Lee MC", 2023),
            ref("2", "Kim KI, Park S", 2023, "Adipose MSCs for knee OA: a meta-analysis"),
            ref("3", "Lamo-Espinosa JM, Mora G", 2016),
            ref("4", "Lee WS, Kim HJ", 2019), ref("5", "Lee AB", 2019)]
    cites = [discover.Citation("Kim et al. (2023)", "Kim", "2023"),
             discover.Citation("Lamo-Espinoza 2016", "Lamo-Espinoza", "2016"),
             discover.Citation("Lee 2019", "Lee", "2019"),
             discover.Citation("Nobody 2020", "Nobody", "2020")]
    got = discover.resolve(cites, refs)
    # the meta-analysis by the same first author doesn't make "Kim 2023" ambiguous
    assert (got[0].status, got[0].source_id) == ("resolved", "MED:1")
    # spelling drift inside a long surname still matches, anchored at its start
    assert (got[1].status, got[1].source_id) == ("resolved", "MED:3")
    assert got[2].status == "ambiguous" and len(got[2].candidates) == 2
    assert got[3].status == "unresolved"


def test_a_citation_that_only_matches_reviews_is_excluded_not_resolved():
    refs = [ref("9", "Smith J", 2020, "Stem cells for knee OA: a systematic review")]
    [r] = discover.resolve([discover.Citation("Smith 2020", "Smith", "2020")], refs)
    assert r.status == "excluded_review"


def test_cited_title_strips_authors_and_quotes():
    assert discover.cited_title("Wong KL, Lee KB, et al. Injectable MSCs in knee OA. Arthroscopy. 2013") \
        == "Injectable MSCs in knee OA"
    assert discover.cited_title("Song, Jun-Seob, et al. “UCB-MSC implantation for knee OA”. Arch Orthop") \
        == "UCB-MSC implantation for knee OA"
