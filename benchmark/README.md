# The benchmark

`questions.yaml` holds 25 questions about MSC therapy for knee osteoarthritis.
Their expected answers come from the eight meta-analyses in the answer key.
rrsrch answers each question from the studies those meta-analyses included,
never from the meta-analyses themselves, and is scored on three numbers:

- **faithfulness**: the share of report sentences the citation gate passes,
- **coverage**: the share of each question's expected `points` the answer makes,
- **rejection rate**: what the reader and the gate refused, by rule.

A report that matches the expected `verdict` but skips the points is incomplete.
So is one that makes the points but gets the verdict wrong.

```bash
uv run rrsrch bench check -v   # every quote verbatim, every id valid, studies per question
```

## Status: draft, needs review

The questions and expected answers are drafts. They were written from 293
findings extracted from the meta-analyses' text, and every quote is checked by
machine. A domain researcher still needs to confirm the verdicts and points
before any score from them is published. Tables and figures were not read, so
forest-plot details are missing.

Eight questions have a `contested` verdict, where the meta-analyses disagree.
These are the questions the engine exists for. A good answer says that the
evidence disagrees and why, rather than picking a side.

## What reading the answer key turned up

The meta-analyses are the benchmark, not the truth. Reading them for the
benchmark found problems that a reviewer, and later the skeptic, should know
about. Where a meta-analysis's words and its statistics disagree, `effect`
follows the statistic and a `note` says so.

| Meta-analysis | Problems found |
|---|---|
| 33407686 (2021, 9 RCTs) | The abstract calls IKDC and WOMAC total improvements both "significant" and "not statistically significant". The 3-month WOMAC total favours the control (WMD 3.35, CI 0.01 to 6.69) but is reported as no difference. A 24-month VAS WMD of -24.98 next to -7.04 at 12 months suggests 0-10 and 0-100 scales were pooled. Multi-arm trials may double-count controls. |
| 36601089 (2022, ADMSCs, 4 RCTs) | The abstract on Europe PMC is garbled. I² of 85-98% comes with very narrow CIs, which suggests fixed-effect pooling. Outcomes are composites built with rescaling multipliers. The paper calls ADMSCs "safe" without pooling adverse events. |
| 39360004 (2024, 25 RCTs, GRADE) | The abstract's p-values (0.76, 0.70) contradict CIs that exclude zero; they look like heterogeneity p-values. One WOMAC estimate lies outside its own CI. The number of placebo-controlled RCTs is given as 6, 7 and 8 in different places. |
| 39629035 (2024, dose) | Its pooled results are before-and-after changes within MSC groups, not comparisons with control. The claimed benefit over control rests on non-overlapping CIs. Study counts don't reconcile (26 "studies", 16 papers, "ninety studies"), and some P-values contradict their CIs. |
| 40055739 (2025, 8 RCTs) | Adverse events are reported as "MD" although they are dichotomous. The methods give 6 and 12 *weeks*; the results are at 6 and 12 *months*. The conclusion claims improved function although 12-month function was not significant. |
| 40407910 (2025, vs other biologics) | The abstract says KOOS pain was significant; the results give p = 0.185 and 0.051. It reports "I2 = 512%". Table 1 has duplicated values across studies. Publication bias was detected. |
| 40886001 (2025, dose, 6 RCTs) | The "≤25M cells" estimate is a meta-regression intercept, not a pooled subgroup, and higher doses were numerically better, which does not support "lower doses are more efficient". The 300 patients don't match Table 1's 324. "Eight RCTs" are six trials. |
| 41013772 (2025, single vs repeated) | The "conventional meta-analysis" is before-and-after change, not a comparison with control. The abstract says repeated injections cause more adverse events; the direct comparison gives P = 0.95, and the discussion says the opposite. The benefit of repeated injections rests on network estimates and SUCRA rankings, while every direct test is non-significant. |
