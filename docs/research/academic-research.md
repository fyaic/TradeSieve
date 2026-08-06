# Academic research

## Research question

How should a sanctions-screening system resolve noisy multilingual identities while preserving recall, explainability, and a manageable human-review queue?

## Relevant work

| Work | Contribution | TradeSieve implication |
| --- | --- | --- |
| [OpenSanctions Pairs: Large-Scale Entity Matching with LLMs](https://arxiv.org/abs/2603.11051) (2026) | A large benchmark derived from real sanctions aggregation across multilingual, cross-script, missing, and set-valued fields; compares a production rule-based matcher and LLMs | Use real sanctions-like slices; focus beyond pair scoring on blocking, clustering, uncertainty, and review |
| [Can LLMs Improve Sanctions Screening in the Financial System?](https://papers.ssrn.com/sol3/Delivery.cfm/fedgfe2025-92.pdf?abstractid=5956312&mirid=1) (Federal Reserve working paper, 2025) | Evaluates LLM families and common fuzzy methods on names/addresses in sanctions screening | Treat LLM matching as an evaluated component, not an assumed improvement |
| [Deep Entity Matching with Pre-Trained Language Models (Ditto)](https://arxiv.org/abs/2004.00584) (KDD 2020) | Casts entity matching as pretrained-language-model pair classification with domain knowledge and augmentation | Useful learned baseline after deterministic candidate generation |
| [Splink: Free software for probabilistic record linkage at scale](https://doi.org/10.23889/ijpds.v7i3.1794) (2022) | Scalable Fellegi-Sunter record linkage and diagnostics | Strong explainable baseline for backfill and multi-field entity linking |
| [Probabilistic Record Linkage and Deduplication after Indexing, Blocking, and Filtering](https://arxiv.org/abs/1603.07816) (2017) | Examines the statistical consequences of candidate blocking/filtering | Candidate generation must be evaluated with the matcher; recall lost before scoring cannot be recovered |
| [Accuracy improvement in financial sanction screening: is NLP the solution?](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2024.1374323/full) (2024) | Studies NLP/text-similarity approaches and limitations around organization/proper names and name variation | Benchmark cross-lingual names and do not substitute generic semantic similarity for identity evidence |

## Findings

### A hybrid matcher is more plausible than an LLM-only matcher

The emerging evidence supports a staged architecture:

1. Deterministic normalization and exact identifier matching.
2. Blocking/candidate retrieval with aliases, script-aware transliteration, phonetics, n-grams, and graph links.
3. Explainable multi-field probabilistic or rule-based scoring.
4. Optional LLM/learned reranking for an uncertainty band.
5. Human disposition for possible/true/false matches.

The LLM sees candidate pairs; it should not search the entire corpus, invent missing identifiers, or silently merge entities.

### Cross-script errors need explicit evaluation

Chinese, Cyrillic, Latin, Arabic, and mixed-script representations create different failure modes. Tests should include:

- transliteration variants and reversed name order;
- legal-form abbreviations and generic company terms;
- aliases and formerly-known-as records;
- exact identifiers with dissimilar names;
- similar names with conflicting identifiers;
- missing dates/addresses and partial registration numbers;
- vessels, banks, and organizations in addition to natural persons.

### Pairwise F1 is not enough

Operational evaluation should include:

- candidate-generation recall before reranking;
- recall and precision by entity type, script pair, source, and identifier completeness;
- false negatives at each enforcement gate;
- alerts/cases per 1,000 screened records;
- reviewer minutes per case and backlog under source deltas;
- calibration and uncertainty-band size;
- P50/P95 latency and bulk throughput;
- stability across source/model/rule versions;
- explanation completeness and reviewer agreement.

### Human labels are a strategic asset

Every reviewer disposition should preserve the compared records, source versions, reasons, and reviewer confidence. With governance and privacy controls, these labels become a domain-specific benchmark for threshold tuning and learned models.

## Proposed experiment sequence

1. Create a synthetic and legally reviewable multilingual fixture set.
2. Establish exact identifier and normalized-name baselines.
3. Evaluate yente and Watchman on the same source snapshot.
4. Evaluate Splink-style probabilistic linkage for ambiguous candidates.
5. Add an LLM reranker only within a controlled uncertainty band.
6. Run blinded human review and measure workload/agreements.
7. Stress-test list deltas, deletions, aliases, and as-of replay.
8. Document model/data cards, limitations, and rollback criteria.

## Research caution

Academic benchmarks measure entity matching, not legal permissibility. A high-performing matcher can still miss ownership/control, goods, route, end-use, licence, or payment restrictions. TradeSieve's final unit of work is a reviewed case, not a model pair label.
