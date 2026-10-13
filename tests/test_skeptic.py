"""Skeptic: claim-vs-claim agreement, by rule. No model, no database."""
from rrsrch.skeptic import agree


def C(text, id="1", source="MED:1", **ctx):
    context = {k: frozenset(v) if not isinstance(v, frozenset) else v for k, v in ctx.items()}
    return agree.ClaimView(id, text, source, context)


def test_benefit_vs_no_difference_on_same_outcome_contradicts():
    a = C("VAS pain improved at 12 months compared with placebo (p = 0.01).", id="1")
    b = C("There was no significant difference in VAS pain at 12 months versus placebo.", id="2",
          source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "contradicts"
    assert r.rule == "direction"
    assert "benefit" in r.detail and "no_difference" in r.detail


def test_two_benefit_claims_on_compatible_outcomes_agree():
    a = C("WOMAC pain improved at 6 months (p < 0.05).", id="1")
    b = C("Pain scores were better at 24 weeks (p = 0.01).", id="2", source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "agrees"
    assert "benefit" in r.detail


def test_womac_vs_mocart_cannot_be_compared():
    a = C("WOMAC function improved at 12 months (p = 0.01).", id="1")
    b = C("MOCART scores showed no difference at 12 months.", id="2", source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "couldnt_compare"
    assert r.rule == "outcomes"


def test_different_comparators_block_comparison():
    a = C("VAS pain improved versus hyaluronic acid at 12 months (p = 0.02).", id="1")
    b = C("VAS pain was not significantly different from placebo at 12 months.", id="2",
          source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "couldnt_compare"
    assert r.rule == "entities"
    assert "comparator" in r.detail.lower() or "hyaluronic" in r.detail or "placebo" in r.detail


def test_ad_msc_and_bm_msc_are_compatible_under_msc():
    a = C("Adipose-derived MSCs improved VAS pain at 12 months (p = 0.01).", id="1",
          cell_type=frozenset({"ad_msc"}))
    b = C("Bone marrow MSCs showed no significant difference in VAS pain at 12 months.", id="2",
          source="MED:2", cell_type=frozenset({"bm_msc"}))
    r = agree.relate(a, b)
    assert r.verdict == "contradicts"          # siblings under MSC still speak to each other


def test_animal_claim_is_out_of_scope():
    a = C("VAS pain improved at 12 months in patients (p = 0.01).", id="1",
          species=frozenset({"human"}), joint=frozenset({"knee"}),
          condition=frozenset({"osteoarthritis"}))
    b = C("Cartilage repaired in the rat osteoarthritis model at 12 months.", id="2",
          source="MED:2", species=frozenset({"rat"}))
    r = agree.relate(a, b)
    assert r.verdict == "couldnt_compare" and r.rule == "scope"


def test_different_follow_up_times_cannot_be_compared():
    a = C("VAS pain improved at 3 months (p = 0.01).", id="1")
    b = C("VAS pain showed no significant difference at 24 months.", id="2", source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "couldnt_compare" and r.rule == "time"


def test_twelve_months_and_fifty_two_weeks_are_the_same_follow_up():
    a = C("VAS pain improved at 12 months (p = 0.01).", id="1")
    b = C("VAS pain improved at 52 weeks (p = 0.02).", id="2", source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "agrees"


def test_harm_vs_safe_on_adverse_events_contradicts():
    a = C("No serious adverse events occurred.", id="1")
    b = C("The incidence of adverse events was significantly higher in the MSC group.", id="2",
          source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "contradicts"


def test_same_paper_can_contradict_itself():
    a = C("Clinical improvement was not significantly different between groups.", id="4",
          source="MED:11869080")
    b = C("The arthroscopic and histological grading score was better in the cell-transplanted group.",
          id="5", source="MED:11869080")
    # different outcomes → couldnt_compare is fine; force same outcome:
    a = C("VAS pain was not significantly different between groups at 12 months.", id="4",
          source="MED:1")
    b = C("VAS pain was better in the cell-transplanted group at 12 months (p = 0.03).", id="5",
          source="MED:1")
    r = agree.relate(a, b)
    assert r.verdict == "contradicts"


def test_claim_with_no_direction_cannot_be_compared():
    a = C("The study included 24 patients with knee osteoarthritis.", id="1")
    b = C("VAS pain improved at 12 months (p = 0.01).", id="2", source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "couldnt_compare" and r.rule == "direction"


def test_spin_benefit_words_over_nonsignificant_p_reads_as_no_difference():
    a = C("VAS pain improved at 12 months (p = 0.06).", id="1")
    b = C("VAS pain improved at 12 months (p = 0.01).", id="2", source="MED:2")
    r = agree.relate(a, b)
    assert r.verdict == "contradicts"   # spin vs real benefit
