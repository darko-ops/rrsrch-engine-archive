# The v1 corpus

**Topic:** MSC therapy for knee osteoarthritis.
**Built:** Oct 12, 2026, with `rrsrch corpus discover`.

## Answer key

Eight open-access meta-analyses, 2021–2025. Each one's Table 1 lists the
studies it included.

| PMID | Year | Meta-analysis | Studies |
|---|---|---|---|
| 33407686 | 2021 | MSCs for knee OA: meta-analysis of RCTs | 8 |
| 36601089 | 2022 | Adipose-derived MSCs in knee OA: meta-analysis of RCTs | 4 |
| 39360004 | 2024 | Advanced therapy with MSCs for knee OA | 23 |
| 39629035 | 2024 | Efficacy and safety of MSC injections for knee OA | 16 |
| 40055739 | 2025 | Efficacy and safety of MSCs in knee OA (RCTs) | 8 |
| 40407910 | 2025 | Adipose and bone marrow MSCs reduce pain but... | 9 |
| 40886001 | 2025 | Single injection of MSCs: a dose-focused meta-analysis | 6 |
| 41013772 | 2025 | Single versus repeated MSC injections | 16 |

To rebuild the manifest from scratch:

```bash
uv run rrsrch corpus discover 40055739 40407910 39360004 33407686 \
    36601089 39629035 41013772 40886001 --topic "MSC therapy for knee osteoarthritis"
```

## What's in it

- **47 studies**, published 2002–2025. 19 have open full text and 28 are
  abstract only. Every report has to say which is which.
- **19 ClinicalTrials.gov registrations**, cited by NCT number in the papers'
  own text.
- **Overlap:** 19 studies appear in more than one meta-analysis, and 10 in
  three or more. Shared trials with different conclusions are where contested
  answers come from.

## Needs a person

**Review (7).** These were matched by Europe PMC search, not by the
meta-analysis's reference list. A first pass found every cited title identical
to the matched title. Confirm each one, then set `review: false`.

| Study | Cited in Table 1 as |
|---|---|
| MED:25548122 | Akgun 2015, matrix-induced MSC vs chondrocyte implantation |
| MED:28752679 | Garay-Mendoza 2018, autologous BM stem cells, pain and function |
| MED:30109404 | Hong 2019, adipose SVF, double-blind self-controlled |
| MED:30522806 | Khalifeh Soltani 2019, placental MSCs, pilot |
| MED:30592390 | Matas 2019, umbilical cord MSCs, repeated dosing |
| MED:31980879 | Song 2020, umbilical cord blood MSC implantation |
| PMC:PMC7273783 | Fiolin 2020, umbilical cord MSCs vs somatotropin vs HA |

**Unresolved (3).** These are not in Europe PMC under any form we could match.
To add one by hand, put it in `studies` with `resolved_by: manual` and the same
`cited_as` text. `discover` will leave it alone after that.

- *Ha CZ et al. 2018*, PRP plus MSCs (Chin J Joint Surg, a Chinese-language journal). Cited by 33407686.
- *Gupta PK et al. 2022*, "Mesenchymal stem/stromal cells: allogeneic, off the shelf, pooled, BM-MSCs (Stempeucel)". Cited by 39360004.
- *Schweich 2022*. 40407910's table gives no title. Cited by 40407910.

## Known gaps

- **The most negative meta-analysis isn't in the answer key yet.** "Intra-articular
  MSC injections are no different from placebo" (PMID 33098949, Arthroscopy 2021)
  has no open full text, so `discover` can't read its Table 1. Its conclusion is
  in the abstract, though, and the benchmark needs it for contested answers. It
  should be added to the answer key without its included-study list. The same
  goes for 38777213 (Osteoarthritis Cartilage 2024, pain).
- **Only ClinicalTrials.gov is searched.** Trials registered only in ChiCTR,
  IRCT, ISRCTN or the EU register are invisible to step 3 for now.
- **Trials are linked only when a paper cites its NCT number.** A paper that
  doesn't is "couldn't match" in step 3, not "unpublished".
