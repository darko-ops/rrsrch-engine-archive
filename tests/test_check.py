from rrsrch.check import evaluate, fields, gate, vocab

H_KNEE_OA = {"species": frozenset({"human"}), "joint": frozenset({"knee"}),
             "condition": frozenset({"osteoarthritis"})}


def test_the_gate_eval_set_has_no_misses():
    """Every case not marked as a known gap must be judged correctly. A new
    rule that breaks one shows up here, by name."""
    misses = [(r.case["kind"], r.case["sentence"], r.verdict.rule, r.verdict.detail)
              for r in evaluate.run().results if not r.correct and not r.case.get("known_gap")]
    assert misses == []


def test_abbreviations_are_case_sensitive_but_words_are_not():
    assert vocab.find("the HA group", "comparator") == {"hyaluronic_acid"}
    assert vocab.find("ha, the results", "comparator") == frozenset()
    assert vocab.find("Hyaluronic Acid injections", "comparator") == {"hyaluronic_acid"}


def test_specific_terms_win_over_general_ones():
    assert vocab.find("bone marrow-derived mesenchymal stem cells", "cell_type") == {"bm_msc"}
    assert vocab.find("adipose-derived MSCs and bone marrow MSCs", "cell_type") == {"ad_msc", "bm_msc"}


def test_an_instrument_name_does_not_set_the_population():
    assert vocab.find("Knee injury and Osteoarthritis Outcome Score", "condition") == frozenset()


def test_numbers_by_kind():
    got = {(n.kind, n.value, n.op) for n in fields.numbers(
        "Twenty-four patients (n = 24) at 52 weeks; 35.7% improved (p < .05), 1,066 vs 957 [12].")}
    assert ("count", 24.0, "=") in got
    assert ("p", 0.05, "<") in got
    assert ("percent", 35.7, "=") in got
    assert ("plain", 1066.0, "=") in got and ("plain", 957.0, "=") in got
    assert not any(v == 12.0 for k, v, _ in got if k == "plain")         # [12] is a reference
    assert any(k == "time" and abs(v - 11.96) < 0.05 for k, v, _ in got)  # 52 weeks in months


def test_vocabulary_names_are_not_numbers():
    assert fields.numbers("T2 mapping and SF-36 were assessed") == ()


def test_benefit_words_over_a_non_significant_p_read_as_no_difference():
    assert fields.clause_effect("VAS pain improved at 12 months (p = 0.06)") == "no_difference"
    assert fields.clause_effect("VAS pain improved at 12 months (p = 0.006)") == "benefit"
    assert fields.clause_effect("did not improve function") == "no_difference"
    assert fields.clause_effect("No serious adverse events occurred") == "safe"


def test_subordinate_clauses_are_split():
    assert fields.clauses("Although A was not different, B was better.") == ["Although A was not different",
                                                                             "B was better."]


def test_a_claim_inherits_and_sharpens_its_source_context():
    ctx = {**H_KNEE_OA, "cell_type": frozenset({"ad_msc"})}
    claim = gate.CitedClaim("c", "MSCs reduced VAS pain at 6 months.", ctx)
    assert gate.check("Adipose-derived MSCs reduced VAS pain at 6 months.", [claim]).passed
    assert gate.check("Bone marrow MSCs reduced VAS pain at 6 months.", [claim]).rule == "entities"


def test_source_context_takes_species_from_the_title_first():
    ctx = gate.source_context("MSC injection for knee osteoarthritis: a randomized trial",
                              "Animal studies in rats were promising. We enrolled 40 patients.")
    # the title names no species and the abstract names two (rats in the background, patients
    # in the study), so species stays unknown rather than guessed
    assert ctx["species"] == frozenset()
    ctx = gate.source_context("BM-MSCs in a rabbit model of knee osteoarthritis", "patients benefit")
    assert ctx["species"] == {"rabbit"}


def test_every_verdict_says_which_rule_decided():
    claim = gate.CitedClaim("c", "WOMAC improved at 12 months (p = 0.01).", H_KNEE_OA)
    v = gate.check("WOMAC improved at 24 months (p = 0.01).", [claim])
    assert (v.passed, v.rule) == (False, "numbers")
    assert [r for r, ok, _ in v.log] == ["cites", "scope", "direction", "entities", "numbers"]
    assert gate.check("WOMAC improved.", []).rule == "cites"
