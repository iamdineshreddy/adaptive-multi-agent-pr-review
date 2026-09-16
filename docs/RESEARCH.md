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

## 3. Related work (to be cited with verification)

| Area | Representative work | Relation to this project |
| --- | --- | --- |
| Multi-agent LLM systems | Wu et al., *AutoGen* (2023); Hong et al., *MetaGPT* (2023); Du et al., *Improving Factuality ... Multiagent Debate* (2023) | Agent fan-out with a coordinating supervisor; we add a deterministic decision/reduction stage after the LLM layer |
| Automated code review | Li et al., *CodeReviewer: Pre-Training for Code Review* (EMNLP 2022); Lu et al., *CodeXGLUE*; Tufano et al., *Empirical Study ... Bug-Fixing Patches* (2021) | Detection side of the pipeline; complements (does not replace) review-comment generation |
| Code-review datasets | Li et al., *CodeReviewer* (EMNLP 2022), `github-codereview` = CodeReview archive, DOI `10.5281/zenodo.6900648` (CC-BY-4.0); Liang et al., *Code Reviews with LLMs* (related corpora) | Primary evaluation data; labelling/licensing per `datasets/README.md` |
| Learning to rank | Burges, *From RankNet to LambdaRank to LambdaMART* (2010); Liu, *Learning to Rank for Information Retrieval* (2011) | ARUM ranking + weight fitting are a linear-LTR formulation over interpretable features |
| Semantics of redundancy | Reimers & Gurevych, *Sentence-BERT* (2019); pgvector (vector similarity index project) | Grouping duplicate findings via normalised cosine + category + proximity |
| Explicit reinforcement/online preference | Standard online-learning / bandit textbooks (Sutton & Barto; Robbins 1952) | Temporal decay + per-repo weight candidates framed as online preference adaptation |
| Orchestration state machines | LangGraph / LangChain; statechart literature (Harel 1987) | Explicit, resumable, diagnosable review lifecycle |

Before submission, every citation must be re-verified (authors, venue, year, arXiv
ID/DOI) and the metadata filled in the bibliography; **no citation is included
that cannot be resolved to a specific, citable artefact.**

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
| B1 (one reviewer, all findings) | Tufano et al., bug-fixing-patch quality (2021); CodeXGLUE code-review comment quality | Unfiltered review emission; the "report everything" baseline |
| B2 (multi-agent, dedup-only) | AutoGen / MetaGPT-style multi-agent scaffolds (without a selection gate) | Fan-out + aggregation without a decision layer |
| B3 (static ARUM weights + budget) | Burges, *From RankNet to LambdaRank* (2010); Liu, LTR survey (2011) | Fixed-weight linear ranking + cap as a non-learnt filter |
| B4 (learned weights + repo memory) | Online preference / implicit-feedback LTR (bandit textbooks; Robbins 1952) | Per-repo weight adaptation from feedback signals |
| B5 (gates, iteration, disagreement) | Li et al., CodeReviewer (2022) system framing | End-to-end review pipeline; compared as a system, not one task |
| A1..A6 (ablations) | — | Internal attribution vs B5; not a literature comparison |

Metric mapping (ARUM §5 vs the literature; where no counterpart exists we
report ours descriptively and say so):

| ARUM §5 metric | Comparable literature metric | Notes |
| --- | --- | --- |
| precision / recall / F1 on actionable findings | CodeReviewer task-accuracy and comment-relevance; CodeXGLUE quality prediction | Same-task comparisons must re-run their benchmark protocol, not reuse their reported tables against different splits |
| redundancy reduction | Duplicate/overlap comment rate (Li et al. data curation) | Descriptive only; no agreed corpus-level metric exists |
| acceptance rate / actionability | Implemented / acknowledged comment rate (Tufano patch adoption) | Proxies only; label provenance differs per study |

Rules: methodological paragraphs carry the citation and are committed now;
numeric cells stay "no executed run — results pending" (no fabrications, never
assert novelty, docs/EXPERIMENTS.md §6). A literature-claims ledger (one row
per claim, mapping to this protocol) is maintained during the final run and
submission, not in advance.

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