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

### Ingestion & annotation protocol

`experiments/ingest/code_review_ingest.py` maps the Zenodo CodeReview archive
into this raw schema without inventing anything:

```
python experiments/ingest/code_review_ingest.py archive.jsonl \
    --annotation annotation.jsonl \
    --out raw.jsonl --excluded excluded.jsonl --manifest manifest.json
```

Mechanical mapping (from the archive, no interpretation): `review_id`
(deterministic content hash, or the archive's `comment_id` when present),
`comment`, `file_path`, `round`, `memory`, optional line numbers;
`implemented_later` = the before/after pair differs; `is_review_comment` and
`related_to_feedback` are structural properties of the archive's review-driven
triplets; **nothing is inferred about whether the change matches the comment**.

The archive supplies **no** `category` / `severity` / `confidence` /
`reviewer`, and no suggestion-matching evidence beyond the changed pair. Those
fields come **only** from an annotation overlay JSONL keyed by the mechanical
`review_id`:

```json
{"review_id": "comment-101", "category": "quality/maintainability",
 "severity": "low", "confidence": 0.9, "reviewer": "annotator-id",
 "implementation": "verbatim", "suggested_fix": "Extract the base URL once."}
```

Overlay fields: `category` (ARUM agent-category taxonomy, docs/AGENTS.md),
`severity`, `confidence` (annotator certainty in [0,1]), `reviewer`,
`round`, `implementation` (`verbatim`|`partial`|`modified`), `explicit_outcome`,
`actionable`, `suggested_fix`, `memory`. Protocol rules:

- `implementation` is recorded **only** when the annotator verified the
  comment's suggestion matches the changed code (the Auth.md evidence bar);
  otherwise it is left absent (→ `changed_unclassified`, excluded).
- `explicit_outcome` comes from an explicit thread record (accept/dismiss/…),
  never from the change alone.
- Records with a missing overlay entry, or an incomplete one, are written to
  `excluded.jsonl` with a machine-readable reason
  (`annotation_required` / `annotation_missing` / `annotation_incomplete:<field>`),
  never silently defaulted.
- Invalid enum values or malformed archives fail loudly (loud data-quality
  gate, never a silent drop).
- The manifest records archive sha256, the DOI/license of the source
  (10.5281/zenodo.6900648, CC-BY-4.0), counts, exclusion reasons, and a
  **labelled preview** produced by running the included records through the
  real `build_corpus` label rules (proving the audit trail, not a separate
  model).

Two-pass flow: (1) ingest without `--annotation` → every record excluded as
`annotation_required` with a stable `review_id`; (2) annotate those ids per the
protocol above; (3) re-ingest with `--annotation` and ship `raw.jsonl` into
`preprocess.py` → `run_all.py`. Bundled demo fixtures:
`samples/code_review_archive_sample.jsonl` +
`samples/code_review_annotation_sample.jsonl` (verification-only).

## Candidate supplementary datasets

`CodeReviewer`, `Review-Reviewer`, and security-focused bench packs (OWASP
context) are documented here with licensing notes before ingestion, per the
research design.