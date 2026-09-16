# Experiments & Evaluation

Research design. **All results are pending experimental validation until scripts run.
Nothing here reports fabricated numbers.**

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

**Phase 17 part 2 (in progress):**

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
- Still pending: `github-codereview` ingestion (license review + preprocessing
  of the real corpus under `datasets/README.md`) and an executed run of the
  full table; literature comparison of the §2 baselines and the §1 label
  evidence (with citations). The plots will be re-rendered from the real run
  (without the verification footnote) once that passes.

All numbers outside part 2 remain "Results pending experimental validation".

---

## 8. Dataset & licensing status (Phase 17 part 2)

- **License review done.** The primary corpus is the Li et al. **CodeReview**
  archive (EMNLP 2022 / arXiv:2203.09095) on Zenodo:
  <https://zenodo.org/records/6900648> (DOI `10.5281/zenodo.6900648`, license
  field verified **CC-BY-4.0** via the Zenodo REST API); collection provenance,
  ingest-time gates (gitignored raw, per-component provenance hash, attribution,
  no redistribution of extracted code, license re-verify) and a scope note
  excluding a same-named third-party HF corpus are recorded in `datasets/README.md`.
- The corpus is **not yet downloaded or ingested**: `datasets/raw/` is gitignored
  and the full-table run awaits the download step (license + provenance hash per
  component per the gates above).
- The bundled `experiments/samples` corpus is a smoke fixture produced by hand
  (5 reviews / 21 comments) — it evidences pipeline behaviour, not results.
- Related-work comparison targets are listed in `docs/RESEARCH.md` §3; any
  baseline/ablation number compared against them will cite the specific work and
  state the comparison protocol at the time of the run.