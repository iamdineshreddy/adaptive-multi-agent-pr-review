# Research Positioning, Related Work & Integrity

This document frames the research contribution, positions it against published
work, and fixes the integrity rules that every experiment and paper artifact must
follow. It is **finalised in Phase 19**; anything not yet executed stays labelled
"Results pending experimental validation".

---

## 1. Contribution statement (defensible)

> This work proposes an integrated adaptive review architecture that separates
> issue detection from finding selection, and combines repository-specific
> feedback memory, redundancy reduction, adaptive review budgeting, and targeted
> iterative analysis within an asynchronous GitHub Pull Request workflow. The
> selection layer is a measurable scoring model (ARUM) with explicit,
> configurable-weights provenance and a reproducibility log, evaluated by
> controlled ablation over a real code-review dataset.

We do **not** claim: "first AI code reviewer", "first multi-agent reviewer",
"no existing system does this", or "100% unique". Related work below is cited
and compared against directly in the final paper.

## 2. Research questions

- **RQ1 — Selection separation.** Does an explicit, scored decision layer
  (post-detection) improve the developer-facing precision of a multi-agent
  reviewer compared with publishing all detections? (B5 vs B2 in
  `docs/EXPERIMENTS.md` §2.)
- **RQ2 — Feedback memory.** Do repository-specific, temporally-decayed
  historical outcomes (actionability / rejection shares) carry signal for which
  findings to publish? (B5 vs B3 / ablation)
- **RQ3 — Budget+gates.** Does a per-risk budget with non-negotiable safety
  gates reduce comment load while preserving critical findings? (B5 vs no-cap
  ablation)
- **RQ4 — Iterative focus.** Does marking resolved/stale regions and re-targeting
  agents to changed regions lower cost and repetition across PR updates?

Each question is answered only from executed runs over the labelled
`github-codereview` slice (or a documented surrogate); no run, no number.

## 3. Related work (verified at the Phase 17 part 2 close-out)

| Area | Representative work | Relation to this project |
| --- | --- | --- |
| Multi-agent LLM systems | Wu et al., *AutoGen* (2023); Hong et al., *MetaGPT* (2023); Du et al., *Improving Factuality ... Multiagent Debate* (2023) | Agent fan-out with a coordinating supervisor; we add a deterministic decision/reduction stage after the LLM layer |
| Automated code review | Li et al., *Automating Code Review Activities by Large-Scale Pre-training* (ESEC/FSE 2022); Lu et al., *CodeXGLUE* (2021); Tufano et al., *An Empirical Study on Learning Bug-Fixing Patches in the Wild via Neural Machine Translation* (TOSEM 2019) | Detection side of the pipeline; complements (does not replace) review-comment generation |
| Code-review datasets | Li et al., *Automating Code Review Activities by Large-Scale Pre-training* (ESEC/FSE 2022), `github-codereview` = CodeReview archive, dataset DOI `10.5281/zenodo.6900648` (CC-BY-4.0) | Primary evaluation data; labelling/licensing per `datasets/README.md` |
| Learning to rank | Burges, *From RankNet to LambdaRank to LambdaMART* (2010, MSR-TR-2010-82); Liu, *Learning to Rank for Information Retrieval* (2009) | ARUM ranking + weight fitting are a linear-LTR formulation over interpretable features |
| Semantics of redundancy | Reimers & Gurevych, *Sentence-BERT* (2019); pgvector (vector similarity index project) | Grouping duplicate findings via normalised cosine + category + proximity |
| Explicit reinforcement/online preference | Standard online-learning / bandit texts (Sutton & Barto 2018; Robbins & Monro 1951) | Temporal decay + per-repo weight candidates framed as online preference adaptation |
| Orchestration state machines | LangGraph / LangChain; statechart literature (Harel 1987) | Explicit, resumable, diagnosable review lifecycle |

Every citation must be re-verified (authors, venue, year, arXiv ID/DOI) with
the metadata filled in the bibliography; **no citation is included that cannot
be resolved to a specific, citable artefact.** *(Executed at the Phase 17
part 2 close-out — verified bibliography, corrections log, and the one drop
are recorded below.)*

### Comparison protocol (Phase 17 part 2)

Two claim classes, never conflated:

- **(a) Methodological / design comparisons** — prose contrast against the
  cited work (agent topology, selection layer, redundancy handling). These need
  no executed numbers, just the citation; they never assert performance.
- **(b) Quantitative comparisons** — only against *executed* runs on the
  labelled corpus slice (§4). A quantitative claim must state **both systems'
  corpus + split + metric + seed**, and compares only same-task, same-metric
  settings. Cross-corpus numeric comparison is prohibited (our slice ≠ their
  benchmark splits).

Baseline-to-literature mapping (used for class (a) context and, where a
same-task setting exists, for a future class (b) comparison):

| Our mode | Nearest literature anchor | Relation |
| --- | --- | --- |
| B1 (one reviewer, all findings) | Tufano et al., bug-fixing-patch prediction (2019); CodeXGLUE comment generation / defect detection | Unfiltered review emission; the "report everything" baseline |
| B2 (multi-agent, dedup-only) | AutoGen / MetaGPT-style multi-agent scaffolds (without a selection gate) | Fan-out + aggregation without a decision layer |
| B3 (static ARUM weights + budget) | Burges, *From RankNet to LambdaRank* (2010); Liu, LTR survey (2009) | Fixed-weight linear ranking + cap as a non-learnt filter |
| B4 (learned weights + repo memory) | Online preference / implicit-feedback LTR (bandit texts; Robbins & Monro 1951) | Per-repo weight adaptation from feedback signals |
| B5 (gates, iteration, disagreement) | Li et al., CodeReviewer (2022) system framing | End-to-end review pipeline; compared as a system, not one task |
| A1..A6 (ablations) | — | Internal attribution vs B5; not a literature comparison |

Metric mapping (ARUM §5 vs the literature; where no counterpart exists we
report ours descriptively and say so):

| ARUM §5 metric | Comparable literature metric | Notes |
| --- | --- | --- |
| precision / recall / F1 on actionable findings | CodeReviewer task-accuracy and comment-relevance; CodeXGLUE comment generation / defect detection | Same-task comparisons must re-run their benchmark protocol, not reuse their reported tables against different splits |
| redundancy reduction | Duplicate/overlap comment rate (Li et al. data curation) | Descriptive only; no agreed corpus-level metric exists |
| acceptance rate / actionability | Implemented / acknowledged comment rate (Tufano patch adoption) | Proxies only; label provenance differs per study |

Rules: methodological paragraphs carry the citation and are committed now;
numeric cells stay "no executed run — results pending" (no fabrications, never
assert novelty, docs/EXPERIMENTS.md §6). A literature-claims ledger (one row
per claim, mapping to this protocol) is maintained during the final run and
submission, not in advance.

### Verified bibliography (executed 2026-10-08, Phase 17 part 2)

Verification sources: Crossref REST API, arXiv API, Zenodo REST API, the
Microsoft Research publication page, the ACL Anthology DOI record (via
Crossref), and the Open Library ISBN record. Software projects resolve to
their repositories / documentation sites.

| Key | Citation (verified metadata) | Verified via |
| --- | --- | --- |
| Li2022 | Li, Z., Lu, S., Guo, D., Duan, N., Jannu, S., Jenks, G., Majumder, D., Green, J., Svyatkovskiy, A., Fu, S., Sundaresan, N. *Automating Code Review Activities by Large-Scale Pre-training*. **ESEC/FSE 2022**, pp. 1035–1047. DOI `10.1145/3540250.3549081`; arXiv:2203.09095. Dataset: Zenodo DOI `10.5281/zenodo.6900648` (CC-BY-4.0) | Crossref event record ("ESEC/FSE '22"); arXiv comment "ESEC/FSE 2022, camera-ready version"; Zenodo API |
| Lu2021 | Lu, S. et al. *CodeXGLUE: A Machine Learning Benchmark Dataset for Code Understanding and Generation*. arXiv:2102.04664 (2021) | arXiv API |
| Tufano2019 | Tufano, M., Watson, C., Bavota, G., Di Penta, M., White, M., Poshyvanyk, D. *An Empirical Study on Learning Bug-Fixing Patches in the Wild via Neural Machine Translation*. **ACM TOSEM 28(4):1–29 (2019)**. DOI `10.1145/3340544`; arXiv:1812.08693 | Crossref; arXiv API |
| Wu2023 | Wu, Q. et al. *AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation*. arXiv:2308.08155 (2023) | arXiv API |
| Hong2023 | Hong, S. et al. *MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework*. arXiv:2308.00352 (2023) | arXiv API |
| Du2023 | Du, Y., Li, S., Torralba, A., Tenenbaum, J. B., Mordatch, I. *Improving Factuality and Reasoning in Language Models through Multiagent Debate*. arXiv:2305.14325 (2023) | arXiv API |
| Burges2010 | Burges, C. J. C. *From RankNet to LambdaRank to LambdaMART: An Overview*. Microsoft Research Technical Report **MSR-TR-2010-82** (June 2010); no journal version exists | microsoft.com/research publication page (journal absence cross-checked against Crossref and the *Machine Learning* table of contents) |
| Liu2009 | Liu, T.-Y. *Learning to Rank for Information Retrieval*. **Foundations and Trends in Information Retrieval 3(3):225–331 (2009)**. DOI `10.1561/1500000016` | Crossref |
| Reimers2019 | Reimers, N., Gurevych, I. *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks*. EMNLP-IJCNLP 2019, pp. 3980–3990. DOI `10.18653/v1/D19-1410`; arXiv:1908.10084 | Crossref / ACL Anthology; arXiv API |
| Harel1987 | Harel, D. *Statecharts: A Visual Formalism for Complex Systems*. **Science of Computer Programming 8(3):231–274 (1987)**. DOI `10.1016/0167-6423(87)90035-9` | Crossref |
| RobbinsMonro1951 | Robbins, H., Monro, S. *A Stochastic Approximation Method*. **The Annals of Mathematical Statistics 22(3):400–407 (1951)**. DOI `10.1214/aoms/1177729586` | Crossref |
| SuttonBarto2018 | Sutton, R. S., Barto, A. G. *Reinforcement Learning: An Introduction* (Adaptive Computation and Machine Learning series). 2nd ed., MIT Press / A Bradford Book (2018). ISBN 978-0-262-0392-46 | Open Library ISBN record |
| Projects | LangGraph, LangChain, pgvector — cited as software projects (repository / documentation URL), no publication metadata | project artefacts |

**Dropped at verification** (per the rule above): *"Liang et al., Code
Reviews with LLMs (related corpora)"* — an arXiv search of `au:Liang` with
`ti:"code review"` (14 results) resolves to no artefact with this title and
no other candidate could be resolved to a specific work; the entry is
removed rather than replaced by a guessed work.

**Corrections applied to pre-existing doc strings** (verification-driven;
no result text changed, no test reads these strings):

| Doc string (before) | Verified artefact | Action |
| --- | --- | --- |
| "EMNLP 2022" (6 occurrences: this doc ×2, `datasets/README.md` ×2, `datasets/DATASET_MAPPING_PROPOSAL.md`, `docs/EXPERIMENTS.md` §8) | ESEC/FSE 2022, DOI `10.1145/3540250.3549081` | corrected in place |
| Title "*CodeReviewer: Pre-Training for Code Review*" | *Automating Code Review Activities by Large-Scale Pre-training* (CodeReviewer is the model that paper introduces) | corrected in place |
| Tufano "(2021)" | TOSEM 28(4):1–29 (2019), DOI `10.1145/3340544` | corrected in place |
| Liu "(2011)" | FnTIR 3(3):225–331 (2009), DOI `10.1561/1500000016` | corrected in place |
| "Robbins 1952" | resolved to the artefact matching the row's stated role: Robbins & Monro (1951), stochastic approximation | replaced |
| "(Sutton & Barto)" undated | 2nd ed. (2018), ISBN 978-0-262-0392-46 | metadata filled |
| Burges "(2010)" | correct year; artefact is technical report MSR-TR-2010-82 (no journal version) | metadata filled |
| "CodeXGLUE code-review comment quality / quality prediction" | CodeXGLUE's actual tasks (comment generation, defect detection); it has no code-review task | wording tightened to the artefact |

### Executed comparison (Phase 17 part 2, per the protocol above)

**Class (a) — methodological (committed with the tables above; no
performance assertions):**

- **B1 ↔ Tufano2019, Lu2021**: the "report everything" baseline corresponds
  to unfiltered emission; Tufano2019 predicts developer patches on mined
  GitHub histories and Lu2021 measures comment-generation / defect-detection
  tasks under CodeXGLUE splits — design-level correspondence only.
- **B2 ↔ Wu2023, Hong2023, Du2023**: B2 keeps multi-agent fan-out plus
  aggregation and removes the selection gate; the cited systems provide
  orchestration without a publication/decision layer.
- **B3 ↔ Burges2010, Liu2009**: the static mode is a fixed-weight linear
  ranking model over explicit features with a cap — the LTR formulation the
  cited works describe.
- **B4 ↔ RobbinsMonro1951, SuttonBarto2018**: per-repo weight candidates and
  temporal decay are framed as online preference adaptation in the
  stochastic-approximation sense; no bandit policy is evaluated as such.
- **B5 ↔ Li2022**: end-to-end review-pipeline framing; Li2022 supplies the
  corpus and the detection-side reference, not a selection-layer baseline.
- **§1 label evidence ↔ Tufano2019, Li2022**: acceptance/outcome labels come
  from the annotation passes (`datasets/README.md`, `docs/EXPERIMENTS.md`
  §1a) and `implemented_later` traces to Li2022 §3.3's collection procedure;
  Tufano2019-style adoption rates remain a *proxy* (different corpora,
  different provenance) — stated in the metric-mapping row above, no number
  compared.
- **A1–A6**: internal attribution against B5 ("not a literature comparison",
  mapping table).

**Class (b) — quantitative: none claimed, by protocol determination.** Every
candidate setting (CodeReviewer task accuracy, CodeXGLUE comment/defect
tasks, Tufano2019 patch adoption) differs from our 494-record labelled slice
in corpus and/or split and/or metric, and none of the executed runs re-runs
a cited work's benchmark protocol. Under the cross-corpus prohibition the
executed numbers (`docs/EXPERIMENTS.md` §9) therefore stand alone as
our-corpus results with **no numeric literature comparison** — reported as
"NOT COMPARED (protocol)" rather than a fabricated cell.

### Literature-claims ledger

Maintained at the final run per the protocol above (citation verification
executed 2026-10-08).

| # | Claim as published in the docs | Class | Citation | Status / location |
| --- | --- | --- | --- | --- |
| C1 | Multi-agent fan-out with a coordinating supervisor; we add a deterministic decision/reduction stage after the LLM layer | (a) | Wu2023, Hong2023, Du2023 | committed — §3 related-work row 1 |
| C2 | Detection side complements (does not replace) review-comment generation | (a) | Li2022, Lu2021, Tufano2019 | committed — §3 row 2 |
| C3 | Primary evaluation data, licence and attribution provenance | factual (dataset attribution, not performance) | Li2022; Zenodo DOI | committed — `datasets/README.md`, §3 row 3 |
| C4 | ARUM ranking + weight fitting are a linear-LTR formulation over interpretable features | (a) | Burges2010, Liu2009 | committed — §3 row 4 |
| C5 | Redundancy grouping via normalised cosine + category + proximity | (a) | Reimers2019; pgvector | committed — §3 row 5 |
| C6 | Temporal decay + per-repo weight candidates framed as online preference adaptation | (a) | RobbinsMonro1951, SuttonBarto2018 | committed — §3 row 6 |
| C7 | Explicit, resumable, diagnosable review lifecycle (statechart lineage) | (a) | Harel1987; LangGraph | committed — §3 row 7 |
| C8 | Baseline anchors for B1–B5 (mapping table) | (a) | per row | committed — §3 mapping table |
| C9 | Metric mapping vs literature metrics | (a) prose; numeric counterpart **not claimed** | Li2022, Lu2021, Tufano2019 | committed — §3 metric table + class-(b) determination above |
| C10 | Executed B1–B5 / A1–A6 numbers | own run only; **no literature comparison** | — | `docs/EXPERIMENTS.md` §9 |
| C11 | "Liang et al., *Code Reviews with LLMs*" (pre-existing entry) | — | unresolvable | **dropped** at verification (recorded above) |

## 4. Evaluation plan

- **Corpus**: primary `github-codereview` slice under a reviewed license
  (`datasets/README.md`); satellite datasets (`CodeReviewer`, `Review-Reviewer`,
  OWASP-context packs) only after licensing notes.
- **Labels**: research-safe pre-processing per `docs/EXPERIMENTS.md` §1 — code
  change is never auto-labelled acceptance.
- **Setups**: baselines B1–B5 and ablations A1–A6 in `docs/EXPERIMENTS.md`
  §2/§4, all mode switches of one code path.
- **Metrics**: ARUM §5 selection-layer metrics
  (`docs/EXPERIMENTS.md` §3): precision/recall/F1, false-positive rate, comment
  reduction, redundancy reduction, acceptance rate, actionability; plus cost and
  latency once the agent layer is exercised.
- **Reproducibility**: seeds, pinned versions, input hashes, `run_all` script
  (`docs/EXPERIMENTS.md` §5).

## 5. Limitations (stated, not hidden)

- **Dataset distribution**: `github-codereview` reflects public-repo review
  culture; findings may not transfer to private/large-scale codebases.
- **LLM nondeterminism**: detection is sampled by temperature/version; the
  decision layer is deterministic, the detection layer is not.
- **Selection-only surface today**: the harness scores a labelled corpus
  offline; end-to-end cost/latency numbers require the live agent layer.
- **Suppression policy**: budget-capped truncation may hide low-severity but
  real findings; safety gates partially bound this — the effect is part of the
  eval.
- **Feedback sparsity**: real developer feedback is slow and scarce; decay and
  the `learning_min_examples` guard trade reactivity for noisiness.

## 6. Ethics

- Public datasets are used under their licenses; no user data beyond public
  review content; repository isolation so learned behaviour does not leak across
  repos.
- Suppression/auto-exclusion must not cascade review-blind spots: critical and
  high-severity gates are hard policy, and truncation events are logged with the
  decision trace.
- We report workload effects (comment reduction) honestly: fewer comments is
  desirable only if precision is not bought at the cost of missed issues.

## 7. Integrity rules

1. No fabricated experiments, metrics, citations, or claims.
2. Cite existing methods and name alternatives we compare against.
3. Document limitations honestly (dataset distribution, LLM nondeterminism,
   cost).
4. Reproducibility: scripts + seeds + version pinning in `experiments/`.
5. Any unpublished result is labelled "Results pending experimental validation".

## 8. Paper scaffolding (filled only with real results)

- Abstract / intro motivating detection-vs-decision separation.
- Architecture diagram (AD) + ARUM formulation + inference (§3 of ARUM.md).
- Baselines/ablation tables (from executed `run_all` outputs).
- Cost & latency analysis (requires live agent layer).
- Ethical considerations (§6) + limitations (§5).
- Bibliography with verified citations (§3).