# Datasets

## github-codereview (primary)

A public dataset of real-world GitHub code-review discussions is the primary
evaluation corpus (docs/EXPERIMENTS.md §1). Raw files live under `datasets/raw/`
(gitignored) — the labelling pipeline never ships the raw dataset into the
repository.

### Licensing

Dataset licensing is reviewed and recorded **here, before ingestion**: the
`github-codereview` corpus is subject to the original dataset's license and the
upstream repositories' licenses; no ingested slice is used without a compatible
license note and provenance hash in the preprocess manifest.

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