# Experiments & Evaluation

Research design + executed-run record. **Nothing here reports fabricated
numbers.** The B1–B5 / A1–A6 table has been executed on the real corpus (§9);
every number lives in `experiments/results/`, produced by
`experiments/run/run_all.py` and never hand-edited. Metrics the harness cannot
measure are reported as NOT MEASURED, never estimated.

---

## 1. Dataset

Primary dataset: **GitHub CodeReview** (`github-codereview`), a public dataset of
real-world code-review discussions with:

- code context (before/after commits)
- review comments
- outcome/contextual info
- negative examples (non-issues, opinion comments)

Labels are PRE-PROCESSED to research-safe definitions (documented under
`datasets/README.md`):

| raw signal | research label | note |
| --- | --- | --- |
| review comment + later accepted refactor | `ACCEPTED` | only when the change directly implements the suggestion |
| reviewer suggestion implemented verbatim | `FIXED` | |
| partial / different implementation | `MODIFIED` | NOT auto-accepted |
| changes without relation to comment | unrelated | excluded from acceptance counts |
| comment dismissed / closed without action | `DISMISSED` / `REJECTED` | by explicit evidence |
| no response | `IGNORED` | |

Explicit rule: **"code changed" is never itself labelled "developer accepted"** (see
`docs/Auth.md`; the labeller requires an explicit/arguable-mapped outcome).

### 1a. Annotation methods (disclosure)

The judgement fields for the seeded sample (`file_path`, `category`, `severity`,
`implementation`, `explicit_outcome`) were filled by **assisted annotation**: an
LLM annotator (model `big-pickle`, acting through the coding agent) applied the
rubric in `datasets/ANNOTATION_GUIDELINES.md` using GitHub's own review records
for each sampled PR as the evidence base (retrieved by
`experiments/ingest/github_evidence.py`; path evidence exact 484 / fuzzy 10 /
none 6 — none → row excluded, never guessed). This is a **model-assisted pass,
not a human-annotated gold set**; a human re-check of a subset is recommended
before publication. Rubric decisions that shape the labels:

- **Rule Q** — a comment with no concrete change request gets
  `implementation: null` and is classified only through `explicit_outcome`.
- **Rule H** — `implementation` ∈ {verbatim, partial, modified, absent} with
  confidence caps; bare `nit:` → info severity, `nit:` with rationale → low.
- **Pass B (outcomes)** — `explicit_outcome` ∈ {accepted, dismissed, rejected,
  null}: accepted = author confirms done / reviewer accepts the rationale;
  **Rule B** additionally maps the comment author's later APPROVED or positive
  verdict body ("LGTM", "Awesome!", ":shipit:") to their earlier comment's
  subject — i.e. some acceptance evidence is PR-level rather than
  comment-level (a disclosed rubric choice); dismissed = the reviewer retracts
  their own suggestion or the review is DISMISSED; rejected = the author
  explicitly declines with no reviewer withdrawal; otherwise `null` → excluded
  as `changed_unclassified`.

Pass-B totals over the 58 rows carrying an outcome: 40 accepted, 5 dismissed,
2 rejected, 11 null (excluded). Four sampled rows carry archive-corrupted
hunks (Varnish 503 text in the source archive — a genuine dataset defect);
their judgements rest on thread evidence and are recorded as a limitation.

Other datasets: candidates are `CodeReviewer`, `Review-Reviewer`, security-focused
bench packs (e.g. OWASP-context datasets). Licensing is documented in
`datasets/README.md` before ingestion.

---

## 2. Baselines

| # | Baseline | Selection mechanism |
| --- | --- | --- |
| B1 | Single LLM reviewer | one agent prompt; all findings published (cap = none) |
| B2 | Multi-agent, no adaptive selection | all agent findings published after dedup-only |
| B3 | Multi-agent + ARUM (static heuristic weights) | ARUM with default weights; budget |
| B4 | Multi-agent + ARUM + feedback memory | ARUM with learned weights + RAG + decay |
| B5 | Full system | B4 + iterative review + disagreement handling + safety gates |

Each baseline is a **mode switch** in the same codebase (single code path exercising
the layers it declares), not a separate hand-wired pipeline. This keeps ablations
honest.

## 3. Metrics

Precision, Recall, F1, False-Positive Rate, Comment Reduction, Redundancy Reduction,
Developer Acceptance Rate, Actionability, Review Latency, Token Usage, Estimated Cost,
Queue Throughput, Iteration Efficiency — definitions in `docs/ARUM.md` §5.

## 4. Ablation studies

Remove one component at a time from B5:

- no feedback memory (weights frozen, no decay)
- no RAG (no retrieval context)
- no redundancy filtering (raw finding count)
- no adaptive budget (no cap / cap=∞)
- no iterative review (full re-review each round)
- no disagreement handling (average verdict, no escalation)

Report delta vs B5 on the same seed/test split.

## 5. Logging & reproducibility

- Every decision logs: ARUM version, 8 features, weights, budget, gates, redundancy
  group; reruns reproducible.
- Runs output CSV + JSON metrics + plots under `experiments/results/`.
- Seed/version-pinned deltas; hash of inputs recorded.
- A single `run_all` script reproduces the full experiment table.

## 6. Integrity contract

- No invented numbers. Any table with no executed run shows "Results pending
  experimental validation".
- Citations for dataset/licensing included; baselines compared against literature
  without claiming novelty-by-assertion (see `docs/RESEARCH.md`).

---

## 7. Phase 17 status

**Phase 17 part 1 (done):** the harness is built and unit-tested. What exists:

- Label pre-processing implementing §1's decision table
  (`backend/app/experiments/labeling.py`, `datasets/README.md`) plus the
  `experiments/preprocessing/preprocess.py` CLI producing the labelled corpus
  artifact + provenance manifest.
- Baselines B1..B5 and ablations A1..A6 as declarative mode switches
  (`backend/app/experiments/modes.py`) executed through the **same**
  `app.adaptive` feature/score/select machinery (`runner.py`), with the ARUM §5
  metrics (`metrics.py`), CSV/JSON outputs and the ablation-vs-B5 delta table
  (`experiments/run/run_all.py`).

What the harness deliberately does **not** claim:

- Runs over the bundled `experiments/samples` corpus are smoke verification,
  not research numbers (the sample is 5 reviews / 21 comments and is flagged as
  such in every payload).
- The agent layer (LLM detection, latency/token/cost) is not invoked; findings
  come from the labelled corpus, so the harness measures the selection layer
  deterministically and offline.

**Phase 17 part 2 (harness + corpus chain + literature comparison done):**

- Plotting tooling is **authored and executed (verification-only)**: the
  `plots` extra (`backend/pyproject.toml`) brings in matplotlib
  (`app/experiments/plots.py`), and the thin CLI
  `experiments/run/plot_summary.py` renders `plot_metrics.png` +
  `plot_deltas_vs_b5.png` next to the harness artifacts. Figures drawn from the
  bundled smoke corpus carry an explicit **VERIFICATION-ONLY** footnote baked
  into the image (honesty contract §6); the generated PNGs/CSVs are gitignored
  (`experiments/results/*`, only `.gitkeep` tracked). The pure data layer
  (`load_summary`/`render_figures`) is covered by `tests/test_experiments_plots.py`
  (8 tests) and the renderer self-describes when the extra is missing.
- **Real-corpus chain executed (done):** ingestion + license review (§8), the
  annotation pass (§1a), pass-2 ingest → `preprocess.py` → `run_all.py` on
  `datasets/annotation/pass2/raw.jsonl` → artifacts + re-rendered plots under
  `experiments/results/` (§9). Literature comparison: **executed** per
  `docs/RESEARCH.md` §3 — verified bibliography, class-(a) prose comparisons
  for the §2 baselines and §1 label evidence, class-(b) determination (no
  cross-corpus numeric claim), and the claims ledger.

**Harness corrections made during the first real run (disclosed):**

1. **Publication set (bug fix).** `runner._outcome` counted every candidate
   handed to selection as published — it ignored each decision's `selected`
   flag — so budgets/gates could never move the ARUM §5 ratios (comment
   reduction and false-positive rate were structurally 0 and recall
   structurally 1). Fixed to count only decisions flagged `selected`;
   regression test
   `test_budget_is_per_review_and_metrics_use_publication_set`.
2. **Budget scope (conformance fix).** The harness invoked `select_decisions`
   once over the whole corpus, applying the cap corpus-wide. ARUM §7 and the
   production orchestrator define the budget **per review**, so `_select` now
   groups by `review_id` and applies cap/gates per review (the regression test
   pins both halves: 2 selected per review, not 2 corpus-wide; 4 of 6
   published, not all 6).

Both fixes make the code match the metric and budget definitions already in
ARUM §5/§7 — no metric definition, baseline or ablation was redefined.

---

## 8. Dataset & licensing status (Phase 17 part 2)

- **License review done.** The primary corpus is the Li et al. **CodeReview**
  archive (ESEC/FSE 2022 / arXiv:2203.09095, DOI `10.1145/3540250.3549081`) on Zenodo:
  <https://zenodo.org/records/6900648> (DOI `10.5281/zenodo.6900648`, license
  field verified **CC-BY-4.0** via the Zenodo REST API); collection provenance,
  ingest-time gates (gitignored raw, per-component provenance hash, attribution,
  no redistribution of extracted code, license re-verify) and a scope note
  excluding a same-named third-party HF corpus are recorded in `datasets/README.md`.
- **Ingestion mapper authored + verified on the sample chain.** 
  `app/experiments/ingest.py` + the `experiments/ingest/code_review_ingest.py`
  CLI map mechanical archive fields and splice the annotation overlay under the
  documented `datasets/README.md` protocol; records the overlay does not fully
  cover are excluded with machine-readable reasons (nothing inferred). Verified
  end-to-end: bundled archive sample → `preprocess.py`-shaped `raw.jsonl` →
  `run_all.py` executes (verification-only). Each overlay entry's
  `implementation`/`explicit_outcome` is an annotation judgement, not a
  pipeline inference (docs/Auth.md).
- **Corpus downloaded and verified; mapping approved and implemented.**
  `Code_Refinement.zip` (1,168,582,011 bytes, MD5 matches Zenodo) is extracted
  under gitignored `datasets/raw/Code_Refinement/` (`ref-train/valid/test.jsonl`,
  13,104 rows in `ref-test`). The archive/schema mismatch (no `file_path`, field
  names `ghid`/`comment`/`old`/`new`) was resolved in
  `datasets/DATASET_MAPPING_PROPOSAL.md` with evidence V1–V7 — including paper
  §3.3 confirmation that `new` is an **observed later commit**, not a synthesised
  target — and applied to `ingest.py`: PR-level `review_id` (`{repo}#{ghid}`),
  per-comment `annotation_key` for the overlay, `file_path` as an annotation
  field, line numbers from the old side of `hunk`.
- **Pass 1 executed on the real corpus sample:** seeded PR-cluster sample of 500
  rows / 426 PRs (55 multi-comment PRs) from `ref-test.jsonl` →
  `datasets/annotation/` (`sample.jsonl`, `annotation_required.jsonl`,
  `annotation_draft.jsonl` with every judgement field null, `aid_manifest.json`);
  ingest CLI confirms 500/500 excluded as `annotation_required` with DOI +
  archive sha256 in the manifest. Sampling, aid and mapper are covered by
  tests (determinism, no-prefill, key agreement).
- **Full table executed (done):** annotation pass (§1a) → 500 judgements →
  494 rows with resolvable `file_path` → pass-2 ingest (`pass2/manifest.json`
  carries DOI + license + archive sha256 + `annotation_provenance`) →
  `preprocess.py` (494 → 483 labelled rows, 11 `changed_unclassified`,
  corpus sha256 `d74e66f9…`) → `run_all.py` (§9). Corpus limits measured
  during that run and stated in the results: `implemented_later` is constant
  `True` by construction; no review rounds exist (A5 ≡ B5 by construction,
  measured delta 0); only 176/10,712 PRs share a line bucket and the sampled
  reviews hold at most 4 candidate groups each, so the per-review budget of 10
  never binds (`truncated_by_budget = 0` in every run); there are no `critical`
  rows and no redundancy group ≥ 3 members, so the mandatory/suppression gates
  never fire (protected = 2, mandatory = 0, suppressed = 0); `IGNORED` is
  unreachable in this sample (no-response rows become `changed_unclassified`);
  6 rows were excluded for unresolvable paths and 4 rows have corrupted hunks
  in the source archive.
- The bundled `experiments/samples` corpus is a smoke fixture produced by hand
  (5 reviews / 21 comments) — it evidences pipeline behaviour, not results.
- Related-work comparison targets are listed in `docs/RESEARCH.md` §3.
  **The comparison protocol is authored** (§3 "Comparison protocol"): two claim
  classes (methodological prose vs executed-number comparisons), a
  baseline-to-literature mapping for B1–B5/A1–A6, a metric mapping, and the
  cross-corpus-comparison prohibition. Any baseline/ablation number compared
  against a cited work will cite the specific work and satisfy the protocol at
  the time of the run; numeric cells stay "no executed run — results pending"
  until then.

---

## 9. Executed run (real corpus)

Commands (reproduce every artifact; the numbers below are transcribed verbatim
from `experiments/results/summary.csv` — never hand-edited):

```text
python experiments/run/run_all.py --corpus datasets/annotation/pass2/raw.jsonl --seed 0 --results-dir experiments/results
python experiments/run/plot_summary.py --results-dir experiments/results --label "github-codereview pass2 (494 annotated records), seed 0 - LLM-assisted annotation, see docs/EXPERIMENTS.md"
```

Artifacts: `experiments/results/{summary.csv, summary.json, runs/*.json,
plot_metrics.png, plot_deltas_vs_b5.png}` (gitignored, regenerable). Execution
block: seed 0, Python 3.11.0, commit `1b8bbb2` (dirty), offline
selection-layer harness — no LLM invoked. Corpus: 494 rows / 483 included /
11 excluded, raw sha256 `d74e66f998bbb672fd88ff5b5811f35a12d927e07801baebbdd873cc7115046c`.

| mode | Precision | Recall | F1 | FPR | Comment red. | Redundancy red. | Acceptance | Actionability | selected | candidates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B1 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | n/a | 0 | 0 |
| B2 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |
| B3 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |
| B4 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |
| B5 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |
| A1 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |
| A2 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |
| A3 | 0.861284 | 1.000000 | 0.925473 | 0.000000 | 0.000000 | 0.000000 | 0.861284 | 0.981366 | 483 | 483 |
| A4 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |
| A5 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |
| A6 | 0.860996 | 1.000000 | 0.925307 | 0.000000 | 0.000000 | 0.002070 | 0.860996 | 0.981328 | 482 | 482 |

Reading the table (measured, not assumed):

- **B1 is NOT MEASURABLE on this corpus.** Its filter `reviewer == "primary"`
  matches 0 rows because the approved mapping (`datasets/DATASET_MAPPING_PROPOSAL.md`
  D5) attributes every row to one of the five agent keys; the `0.000000` cells
  are zero-division artifacts of an empty configuration, **not** a performance
  result. Disposition: reported as not measurable (no mode redefinition).
- **B2–B5 and A1/A2/A4/A5/A6 are numerically identical because the corpus
  leaves the adaptive layers nothing to act on** (all counters measured): the
  per-review budget of 10 never binds (max 4 candidate groups per review,
  `truncated_by_budget = 0`), the mandatory gate finds no `critical` row, the
  suppression gate needs a redundancy group ≥ 3 (max group size 2) plus
  low severity/confidence, and ranking changes (memory/RAG/learned weights/
  disagreement) only reorder a set that is published in full. Recall = 1.0
  because nothing real is ever withheld; comment reduction = 0 because the
  budget never truncates; FPR = 0 because nothing is suppressed.
- **A3 is the only ablation with a measured delta:** dedup off → 483
  candidates (+1 redundant member), redundancy reduction 0.002070 → 0.000000,
  precision +0.000288, F1 +0.000166, actionability +0.000038 (delta table in
  `summary.json`).
- **A5 ≡ B5**: no review rounds exist in the dataset (D6), measured delta 0.

**NOT MEASURED** (harness is selection-layer only, §7 — reported, never
estimated): review latency, token usage, estimated cost, queue throughput,
iteration efficiency (agent-layer metrics), and NDCG / MRR / findings-per-PR
(no ARUM §5 definition exists for them). Redundancy *rate* and findings per PR
at candidate level are derivable from the artifacts; the ratios above cover the
§3 metric list that `metrics.py` implements.

Determinism: for seed 0 every `runs/*.json` payload is byte-identical across
reruns (no timestamps inside `run_mode`; the timestamp lives only in the
`execution` block of `summary.json`).