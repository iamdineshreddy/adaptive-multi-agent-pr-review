# Dataset mapping proposal — approved decisions + verification record

**Status: APPROVED and IMPLEMENTED.** Decisions D1, D2, D5, D6 were approved by
the requester (F1 / PR-level `review_id` / agent key / report A5 honestly); D3,
D4 and D7 were resolved by evidence (below). The mapping is applied in
`backend/app/experiments/ingest.py` (`ARCHIVE_ALIASES`, PR-level
`review_id`, per-comment `annotation_key`, `file_path` from the overlay,
old-side hunk lines) and documented in `datasets/README.md`. `corpus.py`,
`labeling.py`, `modes.py` and `runner.py` remain untouched.

**Why this exists:** `datasets/README.md` defines the raw corpus schema and the
ingest mapper originally required `repo`, `pr_number`, `file_path`,
`review_comment`, `code_before`, `code_after`. The real Li et al. CodeReview
archive (Zenodo 10.5281/zenodo.6900648) does **not** carry those names, and
carries no file path at all. The mapper had been authored and verified only
against the bundled hand-made fixture
(`experiments/samples/code_review_archive_sample.jsonl`), so the mismatch only
surfaced when ingesting the real corpus. This document records the evidence,
the decisions and their outcomes.

---

## 1. Evidence (how the archive schema was established)

Read-only inspection; no dataset file has been committed (`datasets/raw/` is
gitignored) and no download is redistributed.

| Source | Method | Rows inspected |
| --- | --- | --- |
| `Code_Refinement/ref-test.jsonl` | partial inflate of the local (truncated) zip | 13,104 complete rows |
| `Code_Refinement/ref-train.jsonl` | HTTP range request + partial inflate | 56 complete rows |
| `Comment_Generation/msg-train.jsonl` | HTTP range request + partial inflate | 61 complete rows |
| `Diff_Quality_Estimation/cls-test.jsonl` | HTTP range request + partial inflate | 66 complete rows |

Observed fields (frequency uniform over every row inspected):

| Archive | Fields |
| --- | --- |
| `Code_Refinement` (`ref-*.jsonl`) | `repo`, `ghid`, `ids`, `lang`, `comment`, `old`, `new`, `old_hunk`, `hunk`, `oldf` |
| `Comment_Generation` (`msg-*.jsonl`) | `id`, `msg`, `oldf`, `patch`, `y` |
| `Diff_Quality_Estimation` (`cls-*.jsonl`) | `id`, `idx`, `proj`, `lang`, `msg`, `oldf`, `patch`, `y` |

Measured facts from the 13,104 `ref-test` rows:

- `repo` e.g. `spotify/luigi`; `ghid` int (e.g. 2789); `ids` = `[int, sha40, sha40]`
  with `ids[0] != ghid` (a stable per-record identifier) and two 40-char commit shas.
- `hunk` / `old_hunk` headers parse in **13,104 / 13,104** rows (`@@ -a,b +c,d @@`).
- `old == new` in **0 / 13,104** rows (see decision D3 — this is a validity issue,
  not a formatting one).
- 10,712 distinct `repo#ghid` groups; 4,117 rows belong to a PR with >1 comment
  (max 9), so PR-level grouping is possible.
- `lang` spread: py 2900, go 2889, java 2206, cpp 1308, js 1064, php 1032, .cs 738, c 488.
- **No file-path field exists in any of the four archives.**

### 1b. Verification performed before implementation (2026-10-07)

Run read-only against the extracted `datasets/raw/Code_Refinement/ref-test.jsonl`
(gitignored; probes kept outside the repo):

| # | Question | Method | Result |
| --- | --- | --- | --- |
| V1 | Is `new` observed or synthesised? | Li et al., ESEC/FSE 2022 §3.3 (ar5iv mirror of arXiv:2203.09095) | **Observed.** "when the reviewer writes a comment on the code diff D:C0→C1 and the revised source code lines in C1 are further modified to a newer version C2 in a later commit … Then the triplets (C1,Rnl,C2) are collected to build the code refinement dataset." |
| V2 | Filtered to comments followed by a change? | same §3.3 | **Yes, by construction** ("we check all the commits in this pull request to find whether there is a later commit that updates this part of code again"; ambiguous multi-comment/multi-revision samples are removed) → confirms D3. |
| V3 | What is `ghid`? | GitHub API, `projectcalico/felix` #1853 and `tenzir/vast` #466 | **PR number** (both PRs exist at those numbers). D7 confirmed as an observation, not an assumption. |
| V4 | What is `ids`? | same API check | `ids[1]` = the review-time `original_commit_id` (C1), `ids[2]` = the later revision `commit_id` (C2). `ids[0]` matches no GitHub field → **UNVERIFIED** (treated as an opaque record id, used only as overlay-key input). |
| V5 | Which side of `hunk` is the comment target? | 5,000-row content comparison | Marker-stripped `old` **equals** the old side of `hunk` in **4,982/5,000** rows (`old_hunk` matches in only 15/5,000) → `hunk` is the C1→C2 refinement change and its `-` range is the reviewed code. |
| V6 | Are `old`/`new` plain code? | same 5,000 rows | **No**: 4,573/5,000 `old` and 4,332/5,000 `new` contain diff-marker lines (`+`/`-` at column 0). Preserved verbatim — never stripped, never normalised. |
| V7 | Was a path collected originally? | Li et al. §3.2 + released keys | ETCR collected "the git commit hash and changed file name related to the comments", but the released rows contain only `comment, ghid, hunk, ids, lang, new, old, oldf, old_hunk, repo` → the path exists upstream and is recoverable from the PR view (supports F1). |

---

## 2. Proposed mechanical mapping (no interpretation)

| raw schema field | archive field | notes |
| --- | --- | --- |
| `review_id` | `repo` + `ghid` | PR-level `{repo}#{ghid}` (D2 approved) |
| *(overlay key, not a record field)* | `comment_id`, else `repo\|ghid\|ids[0]\|comment` | per-comment `annotation_key`, mirrored to `provenance.annotation_key` (D2 approved) |
| `repo` | `repo` | as-is |
| `pr_number` | `ghid` | GitHub PR number (V3) |
| `review_comment` | `comment` | as-is |
| `code_before` | `old` | as-is; diff-marked region (V6), never rewritten |
| `code_after` | `new` | as-is; **observed later commit C2** (V1), diff-marked region (V6) |
| `line_start` / `line_end` | derived from `hunk` header | `@@ -a,b +c,d @@` → `a .. a+b-1` (V5); explicit `line_start`/`line_end` win when present |
| `file_path` | *(absent from every archive)* | **annotation field** (D1 approved): overlay must supply it, else `annotation_incomplete:file_path` |
| `memory` | *(absent)* | `{}` unless the overlay supplies it (feeds B4/B5 memory features) |
| `category`, `severity`, `confidence`, `reviewer`, `suggested_fix`, `actionable`, `implementation`, `explicit_outcome`, `round` | **annotation overlay only** | unchanged from the `datasets/README.md` protocol; `reviewer` = agent key (D5) |
| `is_review_comment` | `True` | already the documented ingest decision (archive rows are review-driven triplets) |
| `related_to_feedback` | `True` | same |
| `implemented_later` | `old != new` | constant `True` on this corpus (D3/V2) |
| `provenance` | DOI + content hash | plus `annotation_key` and archive `ids` for audit |

---

## 3. Decisions requiring your sign-off

### D1 — `file_path` does not exist in the archive (blocking)

Options:

| # | Option | Consequence |
| --- | --- | --- |
| F1 | **Annotator supplies `file_path` per record** (recommended) | Path becomes an annotation field like `severity`: evidence-based, auditable. Adds one field to the overlay protocol; unannotated records stay excluded (as today). |
| F2 | Derive a synthetic path from `hunk`/`oldf` | **Rejected**: not a path; fabricates data the archive does not contain. |
| F3 | Placeholder path for all records | **Rejected**: `group_key = review_id\|file_path\|category\|bucket` (`corpus.py`) — a constant collapses the file dimension, merging unrelated files and inflating `redundancy_reduction`. |
| F4 | Exclude records without `file_path` | **Rejected**: excludes 100% of records → empty corpus. |
| F5 | Resolve paths via the GitHub API using `repo` + `ghid` + the two `ids` shas, matching `oldf` content against the PR's files | Mechanically verifiable, but network/rate-limit dependent, fails on deleted/archived repos, and is a larger engineering change. Viable as a fallback to F1. |

`file_path` is not cosmetic: it feeds redundancy grouping, `content_hash`,
`finding_id`, and (in production) RAG file filters.

**Decision (approved): F1 — the annotator supplies `file_path`.**
Implemented: `file_path` removed from `REQUIRED_ARCHIVE_FIELDS`, added to
`ANNOTATION_REQUIRED_FIELDS`, so a record without it is excluded as
`annotation_incomplete:file_path` (never defaulted). The annotation aid deep-links
to `https://github.com/{repo}/pull/{ghid}` so the path can be resolved from the
PR view (supported by V7).

### D2 — `review_id` granularity (blocking for redundancy/ablation validity)

`_group_key` (`corpus.py`) is `review_id|file_path|category|bucket`, so candidates
group **within a `review_id`**. Two granularities:

- **Comment-level** (what `ingest._review_id` produces today: content hash over
  `repo|pr_number|file_path|review_comment`): every row is its own group →
  singleton candidates → `redundancy_reduction` ≡ 0, `agent_agreement` never above
  the singleton baseline, variance/disagreement never fires → **A3 and A6 become
  structurally identical to B5 (delta 0 by construction)**.
- **PR-level** (`repo#ghid`), matching the raw-schema example (`"review_id":
  "pr-123"` in `datasets/README.md`): groups can form across comments in the same
  PR/file/category/line bucket — measurable redundancy, meaningful agreement.

Recommended: **PR-level `review_id`**, with the overlay keyed by a separate
per-record id (`ids[0]`, or the content hash of `repo|ghid|ids[0]|comment`) so one
comment cannot overwrite another's annotation. This requires a small, documented
change in `ingest.py` (split "overlay key" from "record review_id") and a matching
sentence in `datasets/README.md`.

**Decision (approved): PR-level `review_id` = `{repo}#{ghid}`**, overlay keyed by
the per-comment `annotation_key` (prefers `comment_id`, else sha256 over
`repo|ghid|ids[0]|comment`; `file_path` deliberately excluded from the hash so the
key is computable *before* annotation). Implemented in `ingest.py`
(`IngestRecord.review_id` vs `IngestRecord.annotation_key`, mirrored into
`provenance.annotation_key`) and documented in `datasets/README.md`.

### D3 — `implemented_later` is constant on this corpus (validity issue)

`old == new` in 0/13,104 rows, so `implemented_later = (old != new)` is `True` for
every record. Consequences under the existing (unchanged) label rules:

- Without annotation: every record → `changed_unclassified` → excluded (as designed).
- With annotation: labels come **only** from `implementation` / `explicit_outcome`
  (the Auth.md guard already forbids using "code changed" as acceptance evidence).
- The corpus therefore has **no natural `IGNORED` / no-response negatives**, and
  the constant `implemented_later` carries zero discriminative information.

Recommended: document this explicitly as a corpus property (the archive appears to
be filtered to comments that *were* followed by a change), source negatives from
explicit `explicit_outcome` annotation, and record the resulting selection bias in
`docs/EXPERIMENTS.md` limitations. Candidate extra source of negatives in the same
Zenodo record: `Diff_Quality_Estimation` rows with `y=0` (change that did not need
a comment) — only if you want to adopt them, since that widens the corpus scope.

**Decision: document as a corpus property (as recommended)** — confirmed by V2
(the archive is filtered to comments followed by a later change), so the constant
`True` is a property of the source, not of this pipeline. Negatives must come from
`explicit_outcome` annotation; the selection bias is stated in
`datasets/README.md` and must be repeated in the results/limitations.

### D4 — Is `new` an *observed* change or a *synthesised refinement target*?

Task 3 of the CodeReviewer benchmark is "change the code again according to the
review comment", so `new` may be the model's refinement target rather than code
the developer actually wrote. The two commit shas in `ids` suggest an observed
before/after pair, but that is inference, not verification.

**Resolved by V1/V2/V4: `new` is observed.** Li et al. §3.3 collects triplets
`(C1, Rnl, C2)` where C2 is *a later commit in the same pull request*, and the
set is filtered so a later commit addressing the code exists. `ids[2]` resolves to
that commit's `commit_id` on GitHub. Therefore `code_after ← new` and
`implemented_later = (old != new)` are legitimate change evidence — and still
**not** acceptance evidence (Auth.md guard unchanged). Caveat recorded by V6:
`old`/`new` are diff-marked regions, not whole files.

### D5 — What does `reviewer` mean in the overlay?

The raw-schema example shows `"reviewer": "security"` (an agent key,
`docs/AGENTS.md` §1), while the overlay example shows `"annotator-id"`. They cannot
both be right:

- **Agent key** (recommended): makes B1 ("single LLM reviewer"), `distinct_agents`,
  `AgentAgreement` and A6 (disagreement handling) meaningful.
- **Annotator id**: records who labelled the row; B1 then has no reviewer to filter
  on and agreement features degrade to single-agent.

Recommended: `reviewer` = agent key from the AGENTS.md taxonomy (assigned by the
annotator as "which agent would emit this"), plus an optional separate
`annotated_by` field for audit.

**Decision (approved): `reviewer` = agent key.** Implemented in
`ANNOTATION_REQUIRED_FIELDS` semantics and documented in `datasets/README.md`
(overlay example updated to `"reviewer": "quality"`); no separate `annotated_by`
field was requested.

### D6 — `round` is absent → iterative-review ablation (A5) is inert

The archive has no review-round field, so rows default to `round=1` and
`scope="first"` vs `"all"` select the same rows → **A5 ≡ B5 (delta 0 by
construction)**. Options: annotate `round` where a comment thread spans multiple
rounds (archive carries none, so this would be annotation-only), or accept and
report "iteration dimension inert on this corpus" honestly in the results table.

**Decision (approved): run A5 and report the 0 delta honestly** — stated as
"iteration dimension inert on this corpus (all rows round 1)" in the results,
never as a null result of the mechanism. No rounds are invented.

### D7 — `pr_number ← ghid`

`ghid` is an integer GitHub id in the PR/issue id space (PRs share the issue
numbering). Accepted as the PR number; recorded in the manifest as an assumption
with the archive field name preserved in `provenance`.

**Resolved by V3 (observation, not assumption): `ghid` is the PR number** — two
sampled `repo#ghid` pairs resolve to existing pull requests on GitHub.
`ids[0]` remains **UNVERIFIED** and is used only as an opaque key input.

---

## 4. What does **not** change

Label rules (`labeling.py`, Auth.md guard), baselines/ablations (`modes.py`),
metrics (`metrics.py`), the selection runner, provenance/hashing, the exclusion
protocol (records lacking annotation stay excluded with machine-readable reasons),
and the integrity contract ("no executed run → results pending").

## 5. Sequence

1. ✅ MD5-verify `datasets/raw/Code_Refinement.zip`
   (`a4620a024d43394257bade2d65862106`, 1,168,582,011 bytes) and extract.
2. ✅ Apply the approved mapping in `ingest.py` + update `datasets/README.md`.
3. Pass-1 ingest (no annotation) → `annotation_required` list with per-record ids.
4. **Annotation aid**: a draft overlay JSONL prefilled with mechanical context
   only (id, repo, PR link, hunk, comment) — every judgement field left empty for
   the annotator; never prefilled with guesses.
5. Annotation pass over a seeded sample → pass-2 ingest → `preprocess.py` →
   `run_all.py --corpus …` → plots.
6. Results labelled with corpus sha256, seed, split, timestamp, commit.
