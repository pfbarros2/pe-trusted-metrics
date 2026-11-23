# Findings

## The question

Can a fund ask an agent for consolidated LTM Adjusted EBITDA and trust the number, when the portfolio companies do not share a definition of revenue or EBITDA?

This demo builds two fictional ledgers and a fictional fund, computes the fund's answers in Python, and compares two offline stand-ins. It also runs the part of NVIDIA Auto Ontology that can execute without Docker and without a model. It does not pretend the stand-in is that product.

## Real-model rerun (23 Nov 2026)

These are real-model results from `nvidia/nemotron-3-ultra-550b-a55b`, run at temperature 0 with three repeats per condition. Everything after this section describes the earlier scripted stand-ins, which used no model. The model saw the question sentence and the schema, plus the fund definitions file in the grounded condition. It never saw the structured task. The planned second model, `mistralai/mistral-large-4-1t-a52b`, was not run because of time.

Copied from `results/llm_doc_sentences.json`:

- Nemotron 3 Ultra (nvidia/nemotron-3-ultra-550b-a55b), schema-only baseline: 21 of 66 answers correct across 3 repeats (per repeat: 7, 7, 7 of 22; mean 7.00).
- Nemotron 3 Ultra (nvidia/nemotron-3-ultra-550b-a55b), definitions-grounded: 60 of 66 answers correct across 3 repeats (per repeat: 20, 20, 20 of 22; mean 20.00).
- Nemotron 3 Ultra: if the repo SQL guard had blocked every query it rejected, the baseline would have 10 of 66 correct and the grounded condition 52 of 66.
- Nemotron 3 Ultra, baseline: 60 queries ran. The repo SQL guard rejected 43 wrong and 11 correct answers, and passed 2 wrong answers.
- Nemotron 3 Ultra, baseline: Auto Ontology's catalog parser resolved tables and source columns for 60 of 60 executed queries; 52 of them read a column outside the fund's agreed lineage.
- Nemotron 3 Ultra, baseline: 3 of 66 answers needed the one allowed follow-up turn.
- Nemotron 3 Ultra, grounded: 63 queries ran. The repo SQL guard rejected 0 wrong and 8 correct answers, and passed 3 wrong answers.
- Nemotron 3 Ultra, grounded: Auto Ontology's catalog parser resolved tables and source columns for 63 of 63 executed queries; 0 of them read a column outside the fund's agreed lineage.
- Nemotron 3 Ultra, grounded: 2 of 66 answers needed the one allowed follow-up turn.
- Nemotron 3 Ultra: 137 model calls used 344536 prompt tokens and 278368 completion tokens.

### What the model got wrong without definitions

The schema-only model did what a capable analyst would do with only column names. It took the columns that look like the headline: `turnover` and `ebitda` from North's management view, and `GrossBillings` and `EbitdaAfterAddbacks` from West's board pack. For Madeup North's FY2025 Net Revenue (Q01), it used `turnover` in all three repeats, which is billed turnover gross of rebates, including intercompany billing, instead of fund Net Revenue. Using the same headline columns, it named Madeup West as the higher-margin company (Q08) in every repeat, when the fund definitions make it Madeup North. Its consolidated margin change (Q20) has the wrong sign. It scored on the questions where the obvious column happens to be the agreed one (`RecognizedNetSales` for West, `intercompany_billed_gross`, `rejected_addback_functional`), on the month question, and on the two unanswerable questions, where it declined to answer.

### What still went wrong with definitions

The grounded condition missed two questions, in all three repeats each. Neither was a construct error.
- *Q20.* This is a scoring-convention miss. The model's unrounded margin change was within one unit of the fourth decimal of the ground truth in every repeat, but the harness rounds each margin to six decimals first.
- *Q22.* The model answered the like-for-like organic growth question instead of declining it. It computed total consolidated Net Revenue growth on the fund definitions and treated it as organic growth, because the data has no acquisition flags. The ground truth treats the question as unanswerable.

The consolidated sterling totals and the LTM window questions (Q03, Q04 and Q06), which had rounding and date slips in the earlier run (13 Nov 2026), were correct in all three repeats this time. With one model and three repeats, that change is small and may not hold on another run.

Neither miss was caught by the repo guard or by the lineage check on Auto Ontology's parsed columns. The Q20 queries stayed inside the agreed lineage, and Q22 has no agreed lineage to check against.

### What Auto Ontology did in the rerun

Auto Ontology's catalog parser (`parse_query_single`, commit fc43a82600e18b19d159c52023f54f5572f537ac) parsed every executed model query against a catalog built by its own DuckDB connector and extractor. It returned the tables, source columns and join keys, which are stored per answer in `results/llm_auto_ontology_lineage.json`. The only change was replacing the Postgres catalog-store id lookups with pass-through functions, matching upstream's behaviour on an empty store. Its model-backed text-to-SQL graph, semantic compilation and embeddings did not run. The repo's own SQL guard, which is not Auto Ontology, was applied after the fact.

### Per-question results

Copied from `results/llm_sample_results.md`. The counts are correct answers out of three repeats. The answer columns show repeat 1, as raw values before display formatting.

| ID | Ground truth | Baseline answer, repeat 1 | Baseline correct | Grounded answer, repeat 1 | Grounded correct |
| --- | --- | --- | --- | --- | --- |
| Q01 | GBP 46,971,087.20 | 53651200.00 | 0 of 3 | 46971087.20 | 3 of 3 |
| Q02 | GBP 63,172,934.25 | 63172934.25 | 3 of 3 | 63172934.25 | 3 of 3 |
| Q03 | GBP 110,144,021.45 | 105933904.85 | 0 of 3 | 110144021.45 | 3 of 3 |
| Q04 | GBP 18,873,128.33 | 17528647.61 | 0 of 3 | 18873128.33 | 3 of 3 |
| Q05 | GBP 17,726,702.74 | 17101912.42 | 0 of 3 | 17726702.74 | 3 of 3 |
| Q06 | GBP 108,545,786.36 | 115154681.56 | 0 of 3 | 108545786.36 | 3 of 3 |
| Q07 | 17.1350% | 0.144529 | 0 of 3 | 0.171350 | 3 of 3 |
| Q08 | Madeup North Logistics Ltd | Madeup West Assemblies Inc | 0 of 3 | Madeup North Logistics Ltd | 3 of 3 |
| Q09 | Madeup North Logistics Ltd | Madeup West Assemblies Inc | 0 of 3 | Madeup North Logistics Ltd | 3 of 3 |
| Q10 | GBP 9,477,967.20 | 5533184.00 | 0 of 3 | 9477967.20 | 3 of 3 |
| Q11 | USD 11,787,855.19 | 15049246.40 | 0 of 3 | 11787855.19 | 3 of 3 |
| Q12 | USD 5,068,800.00 | 5068800.00 | 3 of 3 | 5068800.00 | 3 of 3 |
| Q13 | GBP 27,334,246.32 | 29008476.97 | 1 of 3 | 27334246.32 | 3 of 3 |
| Q14 | GBP 4,968,000.00 | 1309446.00 | 0 of 3 | 4968000.00 | 3 of 3 |
| Q15 | GBP 1,440,000.00 | 1440000.00 | 2 of 3 | 1440000.00 | 3 of 3 |
| Q16 | 2025-06-30 | 2025-06-30 | 3 of 3 | 2025-06-30 | 3 of 3 |
| Q17 | GBP 9,283,252.18 | 7992960.23 | 0 of 3 | 9283252.18 | 3 of 3 |
| Q18 | 6.3279% | 0.061277 | 0 of 3 | 0.063279 | 3 of 3 |
| Q19 | USD 77,061,184.96 | 77061184.96 | 3 of 3 | 77061184.96 | 3 of 3 |
| Q20 | 0.0225 pp | -0.5549 | 0 of 3 | 0.0224 | 0 of 3 |
| Q21 | unanswerable | unanswerable | 3 of 3 | unanswerable | 3 of 3 |
| Q22 | unanswerable | unanswerable | 3 of 3 | 0.06327939553848272 | 0 of 3 |

## What the comparison showed (stand-in, no model)

The grounded stand-in, which may only use the agreed SQL expressions, matched the Python oracle on every question. The baseline stand-in, which picks a fact-table column by name and uses one spot FX rate on multi-month USD totals, matched on a small subset. Those counts, the tolerances, and the question ids are the recorded sentences below. They are copied from `results/doc_sentences.json`.

The interesting misses are definitional, not arithmetic slips.

Madeup North's fund Net Revenue for a single January month is lower than management turnover, because rebates, deferral, and intercompany billing are treated differently, and fund Adjusted EBITDA is higher than reported EBITDA, because rent and one-offs stay out and the approved add-back comes in. The January figures are in the recorded sentences. Over a full year the same pattern remains: the baseline, summing billed gross turnover, overstates North's FY Net Revenue, and summing reported EBITDA understates fund Adjusted EBITDA.

Madeup West's board EBITDA includes management add-backs the fund mostly declines, and the baseline's revenue column is gross billings rather than recognised net sales. In USD, with no FX involved, the baseline still overstates FY Adjusted EBITDA. Consolidated sterling totals then add a second error: one rate on the window end date, instead of a rate per month.

The ranking flips. On the fund definitions, Madeup North has the higher LTM Adjusted EBITDA margin at December 2025. On the local headline columns, Madeup West does. The baseline, which is effectively scoring those headline columns, names Madeup West. It makes the same mistake on margin growth from the prior LTM. The consolidated margin change even changes sign: the fund figure is a small expansion, and the baseline reports a contraction. Both of those results are quoted below.

Intercompany revenue is a naming trap rather than a missing column. The baseline finds `intercompany_billed_gross` on North, which is the right component, and also finds `IntercompanyPurchasesUsd` on West, which is a cost. It adds them. The agreed definition eliminates North's intercompany revenue and counts zero revenue at West.

Two questions have no agreed definition: return on advertising spend, and organic growth excluding acquisitions. Both stand-ins refuse. That agreement is cheap. The baseline is scripted to stop when no token matches, and the grounded stand-in stops when the concept is absent from the definitions file. A model with only the schema might do the same, or it might not. This run does not measure that.

One question is a false comfort. The month with the highest consolidated Adjusted EBITDA is the same month under both stand-ins, and the definition guard still rejects the baseline SQL. The level of EBITDA in that month is not what was scored. A reviewer who checks only the printed month would miss the wrong columns.

## What Auto Ontology actually did

Docker was not installed. No model key was present. KumoRFM was left off and no table was uploaded. The web stack and MCP server were not started.

Importing the package in the ordinary way failed. `auto_ontology.connectors` stops on a missing HTTP client, because the connector package imports every engine. `auto_ontology.semantic.term_extractor` and `auto_ontology.semantic.deterministic` stop on a missing LangChain client. Term extraction is a model call in that code, so it was not run, and it would have had nothing to call.

What did run is the upstream `DuckDBDatabase` and `extract_tabular_db_data`, loaded from a read-only checkout by importing those modules directly and skipping the package inits. That is their catalog-extraction code, not a reimplementation. It read the three fictional files. The recorded sentences give the table and column counts. `get_pks` and `get_fks` returned empty frames. The tables declare primary keys; those two methods, in the version that was read, do not query them. They return an empty frame. `get_queries` warns and returns no rows, because DuckDB has no query history and the connector looks for a sample CSV that this repo does not provide. `get_views` also returns the engine's own views.

Semantic compilation, embeddings, SQL-attribute suggestion, and the chat path were not run. Nothing in this file should be read as a benchmark of NVIDIA's model.

## How to read the stand-in grounded score

The grounded stand-in receives the metric, the scope, the window, and the currency as fields. It does not parse the English sentence. Its SQL is filled from the YAML expression, then a guard checks column lineage and the FX shape, then DuckDB runs it. The oracle implements the same policy in Python, on purpose, so a mismatch means a bug in one of them. The tests require them to match. A score of a perfect grounded run is evidence that the harness is consistent. It is not evidence that an agent, given only the question, would be right.

## Limitations

The real-model rerun's limitations are listed in the README, in the section on limitations of the real-model rerun. The points below apply to the stand-in.

The baseline is a reproducible heuristic, published in `src/pe_metrics/plans.py` and dumped to `results/baseline_column_choices.json`. It is not an estimate of how often a production model would miss. It ignores a table a diligent person might open.

The books are small and clean. There is no partial month, no restatement, no debt-like item, and no ownership below the whole company. Intercompany amounts are built to tie at the monthly rate after cent rounding.

The agreed definitions were written for the demo. In Auto Ontology, a person can later review and approve a definition. That step was not done, and this file does not claim it.

## Recorded figures

Copied from `results/doc_sentences.json`.

- The stand-in baseline was within tolerance on 3 of 22 questions (13.6%).
- The stand-in grounded condition was within tolerance on 22 of 22 questions (100.0%).
- Money answers match within 0.01, ratios within 0.0000005, and percentage points within 0.00005. Entity names and months must match exactly.
- Baseline question ids within tolerance: Q16, Q21, Q22.
- Grounded question ids within tolerance: Q01, Q02, Q03, Q04, Q05, Q06, Q07, Q08, Q09, Q10, Q11, Q12, Q13, Q14, Q15, Q16, Q17, Q18, Q19, Q20, Q21, Q22.
- Each company fact table has 24 month-end rows, from 2024-01-31 through 2025-12-31.
- The fund consolidates 2 portfolio companies.
- The fictional schedule approves 0.40 of Madeup North's proposed add-back and 0.12 of Madeup West's booked add-back, rounded half away from zero to the cent.
- In 2024-01-31, Madeup North fund Net Revenue was GBP 3,220,000.00 and fund Adjusted EBITDA was GBP 579,600.00. The company's own reported EBITDA that month was GBP 428,000.00.
- In 2024-01-31, Madeup West fund Adjusted EBITDA was USD 835,117.12. The company's own EbitdaAfterAddbacks that month was USD 1,080,719.94.
- LTM Adjusted EBITDA margin at Dec 2025 on the fund definitions was 20.1783% for Madeup North and 14.8721% for Madeup West. The higher fund margin was Madeup North Logistics Ltd.
- The local headline margin over the same months was 10.3133% for Madeup North and 16.9552% for Madeup West. The higher local headline margin was Madeup West Assemblies Inc.
- Q01 ground truth is GBP 46,971,087.20. The baseline stand-in answered GBP 53,651,200.00. The grounded stand-in answered GBP 46,971,087.20.
- Q04 ground truth is GBP 18,873,128.33. The baseline stand-in answered GBP 17,316,743.93. The grounded stand-in answered GBP 18,873,128.33.
- Q08 ground truth is Madeup North Logistics Ltd. The baseline stand-in answered Madeup West Assemblies Inc. The grounded stand-in answered Madeup North Logistics Ltd.
- Q11 ground truth is USD 11,787,855.19. The baseline stand-in answered USD 15,049,246.40. The grounded stand-in answered USD 11,787,855.19.
- Q12 ground truth is USD 5,068,800.00. The baseline stand-in answered USD 5,760,000.00. The grounded stand-in answered USD 5,068,800.00.
- Q15 ground truth is GBP 1,440,000.00. The baseline stand-in answered GBP 2,855,201.08. The grounded stand-in answered GBP 1,440,000.00.
- Q16 ground truth is 2025-06-30. The baseline stand-in answered 2025-06-30. The grounded stand-in answered 2025-06-30.
- Q20 ground truth is 0.0225 pp. The baseline stand-in answered -0.4917 pp. The grounded stand-in answered 0.0225 pp.
- Q21 ground truth is unanswerable. The baseline stand-in answered unanswerable. The grounded stand-in answered unanswerable.
- Q16 matched the ground-truth month 2025-06-30 under the baseline stand-in, and the definition guard still rejected that baseline SQL.
- Auto Ontology source commit fc43a82600e18b19d159c52023f54f5572f537ac from https://github.com/NVIDIA/auto-ontology.git was used read-only.
- Docker was not available, so Docker Compose was not started.
- No NVIDIA or OpenAI-compatible model API key was set, so the model was not called.
- KumoRFM was off and no table was uploaded.
- The web UI on port 3000, the backend on port 3001, the ingestion service on port 3002, the MCP server on port 3003, and Postgres on port 5432 were not started.
- Package imports of auto_ontology.connectors, auto_ontology.semantic.term_extractor, auto_ontology.semantic.deterministic were attempted and did not succeed, because those modules import database engines and a model client that are not installed here.
- DuckDBDatabase and extract_tabular_db_data were loaded from the upstream source by importing those modules directly, skipping the package inits that pull in every engine and the embedding client.
- Python 3.12.3, DuckDB 2.0.0, and pandas 2.3.3 ran that extraction.
- extract_tabular_db_data on north returned 3 tables and 20 columns. 3 of those tables were in the portfolio schemas. get_pks returned 0 rows and get_fks returned 0 rows.
- extract_tabular_db_data on west returned 3 tables and 21 columns. 3 of those tables were in the portfolio schemas. get_pks returned 0 rows and get_fks returned 0 rows.
- extract_tabular_db_data on holdco returned 4 tables and 15 columns. 4 of those tables were in the portfolio schemas. get_pks returned 0 rows and get_fks returned 0 rows.

## Sample results

Copied from `results/sample_results.md`.

| ID | Question | Ground truth | Baseline stand-in | Grounded stand-in | Baseline within tolerance | Grounded within tolerance |
| --- | --- | --- | --- | --- | --- | --- |
| Q01 | What was Madeup North Logistics Ltd's Net Revenue for FY2025, in GBP? | GBP 46,971,087.20 | GBP 53,651,200.00 | GBP 46,971,087.20 | no | yes |
| Q02 | What was Madeup West Assemblies Inc's Net Revenue for FY2025, in GBP? | GBP 63,172,934.25 | GBP 69,498,328.32 | GBP 63,172,934.25 | no | yes |
| Q03 | What was consolidated Net Revenue for FY2025, in GBP? | GBP 110,144,021.45 | GBP 123,149,528.32 | GBP 110,144,021.45 | no | yes |
| Q04 | What was consolidated LTM Adjusted EBITDA at Dec 2025, in GBP? | GBP 18,873,128.33 | GBP 17,316,743.93 | GBP 18,873,128.33 | no | yes |
| Q05 | What was consolidated LTM Adjusted EBITDA at Dec 2024, in GBP? | GBP 17,726,702.74 | GBP 16,798,846.81 | GBP 17,726,702.74 | no | yes |
| Q06 | What was consolidated LTM Net Revenue at Sep 2025, in GBP? | GBP 108,545,786.36 | GBP 122,193,925.76 | GBP 108,545,786.36 | no | yes |
| Q07 | What was the consolidated LTM Adjusted EBITDA margin at Dec 2025? | 17.1350% | 14.0616% | 17.1350% | no | yes |
| Q08 | Which company had the higher LTM Adjusted EBITDA margin at Dec 2025? | Madeup North Logistics Ltd | Madeup West Assemblies Inc | Madeup North Logistics Ltd | no | yes |
| Q09 | Which company had the higher Adjusted EBITDA margin growth from LTM Dec 2024 to LTM Dec 2025? | Madeup North Logistics Ltd | Madeup West Assemblies Inc | Madeup North Logistics Ltd | no | yes |
| Q10 | What was Madeup North Logistics Ltd's Adjusted EBITDA for FY2025, in GBP? | GBP 9,477,967.20 | GBP 5,533,184.00 | GBP 9,477,967.20 | no | yes |
| Q11 | What was Madeup West Assemblies Inc's Adjusted EBITDA for FY2025, in USD? | USD 11,787,855.19 | USD 15,049,246.40 | USD 11,787,855.19 | no | yes |
| Q12 | How much of Madeup West Assemblies Inc's FY2025 management add-backs did the fund decline, in USD? | USD 5,068,800.00 | USD 5,760,000.00 | USD 5,068,800.00 | no | yes |
| Q13 | What was consolidated Net Revenue in Q4 2025, in GBP? | GBP 27,334,246.32 | GBP 30,991,914.86 | GBP 27,334,246.32 | no | yes |
| Q14 | What was the fund's IFRS 16 lease add-back in consolidated FY2025 Adjusted EBITDA, in GBP? | GBP 4,968,000.00 | GBP 5,362,632.00 | GBP 4,968,000.00 | no | yes |
| Q15 | What was intercompany revenue eliminated from consolidated FY2025 Net Revenue, in GBP? | GBP 1,440,000.00 | GBP 2,855,201.08 | GBP 1,440,000.00 | no | yes |
| Q16 | Which month in calendar 2025 had the highest consolidated Adjusted EBITDA? | 2025-06-30 | 2025-06-30 | 2025-06-30 | yes | yes |
| Q17 | What was consolidated Adjusted EBITDA for H1 2025, in GBP? | GBP 9,283,252.18 | GBP 8,636,756.18 | GBP 9,283,252.18 | no | yes |
| Q18 | What was the year-on-year growth in consolidated Net Revenue from FY2024 to FY2025? | 6.3279% | 6.6880% | 6.3279% | no | yes |
| Q19 | What was Madeup West Assemblies Inc's LTM Net Revenue at Jun 2025, in USD? | USD 77,061,184.96 | USD 86,294,720.00 | USD 77,061,184.96 | no | yes |
| Q20 | By how many percentage points did the consolidated LTM Adjusted EBITDA margin change from Dec 2024 to Dec 2025? | 0.0225 pp | -0.4917 pp | 0.0225 pp | no | yes |
| Q21 | What was consolidated return on advertising spend in FY2025? | unanswerable | unanswerable | unanswerable | yes | yes |
| Q22 | What was consolidated like-for-like organic revenue growth excluding acquisitions in FY2025? | unanswerable | unanswerable | unanswerable | yes | yes |

