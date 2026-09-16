# Datasets

## github-codereview (primary)

A public dataset of real-world GitHub code-review discussions is the primary
evaluation corpus (docs/EXPERIMENTS.md §1). Raw files live under `datasets/raw/`
(gitignored) — the labelling pipeline never ships the raw dataset into the
repository.

### Licensing

**Review completed for ingestion (Phase 17 part 2, `docs/EXPERIMENTS.md` §8).**
The `github-codereview` corpus used here is the Li et al. **CodeReview** dataset
(the archive of *Automating Code Review Activities by Large-Scale Pre-Training*,
EMNLP 2022 / arXiv:2203.09095), distributed on Zenodo:

- Record: <https://zenodo.org/records/6900648> — DOI `10.5281/zenodo.6900648`
  (license field verified via the Zenodo REST API: **CC-BY-4.0**).
- Processing code + benchmark splits: `microsoft/CodeBERT` → `CodeReviewer/`
  (Apache-2.0).

Dataset provenance facts (as reported by the collectors, arXiv:2203.09095 §3):
- Content is real-world GitHub pull-request review data (comments, diffs, code
  changes) from nine popular languages, harvested via the GitHub REST API.
- Collection kept repositories that **explicitly permit re-distribution of their
  data** — permissive popular licenses named in the paper (Apache-2.0, GPL-2.0,
  GPL-3.0, MIT, BSD-2.0, BSD-3.0, BSL-1.0) or an explicit re-distribution note
  in the repository's license file.

Gates applied **at ingestion time** (no slice is used without these):

1. `<datasets/raw/>` stays gitignored; the raw download is never committed.
2. The preprocess manifest records a **provenance hash per component**
   (dataset DOI, raw slice hash, upstream repository) — `experiments/preprocessing/preprocess.py`
   already emits this manifest.
3. Attribution to the dataset persists in every derived artifact
   (citation: Li et al., EMNLP 2022).
4. Extracted code snippets are used for **evaluation only and never
   redistributed**; the record's license field is re-verified against the
   Zenodo API at ingest time before any slice is processed.

Scope note: a third-party Hugging Face dataset published under the same
`github-codereview` name (`ronantakizawa/github-codereview`, 2026) is a
**different** corpus (own curation, own license card) and is **not** adopted
here; the primary dataset remains the Li et al. Zenodo record above.

### Input schema (`raw/*.jsonl`)

One review comment per line:

```json
{
  "review_id": "pr-123",
  "file_path": "src/app.py",
  "line_start": 10,
  "line_end": 12,
  "category": "security/xss",
  "severity": "high",
  "confidence": 0.91,
  "reviewer": "security",
  "round": 1,
  "comment": "The signal is interpolated into HTML; escape it.",
  "suggested_fix": "Use a context-aware escaper.",
  "actionable": true,
  "is_review_comment": true,
  "related_to_feedback": true,
  "implemented_later": true,
  "implementation": "verbatim",
  "explicit_outcome": null
}
```

`is_review_comment` distinguishes feedback from opinion/off-topic comments;
`related_to_feedback` marks later changes that implement the suggestion;
`implemented_later` + `implementation` (`verbatim`|`partial`|`modified`) and/or
`explicit_outcome` (`accepted`|`fixed`|`dismissed`|`rejected`|`ignored`) drive
the research labelling. `memory` (per-repo ARUM snapshot shape) optionally
feeds the learned/memory baselines.

### Label rules

`backend/app/experiments/labeling.py` implements docs/EXPERIMENTS.md §1:

| raw signal | research label | rule |
| --- | --- | --- |
| not a review comment | excluded (`not_review`) | |
| change unrelated to comment | excluded (`change_unrelated`) | |
| implemented verbatim, no explicit outcome | `FIXED` | `verbatim` |
| implemented partial/modified | `MODIFIED` | `partial_modified` (never auto-accepted) |
| changed but no implementation evidence | excluded (`changed_unclassified`) | |
| explicit thread outcome (any) | that label (wins over change inference) | `explicit` |
| explicit acceptance without any change | excluded (`acceptance_without_change`) | |
| no change, no response | `IGNORED` | `no_response` |

Guarding principle (docs/Auth.md): **code changed is never itself labelled
developer-accepted.** Every positive label requires suggestion-matching
implementation evidence or an explicit thread outcome.

## Candidate supplementary datasets

`CodeReviewer`, `Review-Reviewer`, and security-focused bench packs (OWASP
context) are documented here with licensing notes before ingestion, per the
research design.