# Annotation guidelines (recorded before the pass started)

**Who annotates.** The overlay judgements for the seeded sample are produced by
**an LLM (the coding agent running this repo, model `big-pickle`)**, reading each
row's archive context plus the GitHub evidence fetched by
`experiments/ingest/github_evidence.py`, and applying the rubric below
mechanically. This is *assisted annotation* and must be disclosed as such in
`docs/EXPERIMENTS.md` and the results paper — an LLM labelling data for a study
that evaluates an LLM-based reviewer is a circularity risk, stated openly rather
than hidden. Every row keeps its evidence (`path_evidence.jsonl`) so a human can
re-check or re-label any judgement; a human review pass over a random subset is
recommended before results are treated as final.

**What is *not* a judgement** (came from records, not from the annotator):

- `file_path` — the `path` of the GitHub review comment the archive row was
  extracted from (`match: exact`), confirmed against the diff when `match:
  fuzzy`, and left `null` when no review comment matches (→ excluded as
  `annotation_incomplete:file_path`, never guessed).
- `round` — absent from the archive; defaults to 1 (proposal decision D6).
- `memory` — absent; stays null (`{}` at ingest).

## Rubric (applied in this order per row)

1. **`category`** — the single primary category from the `docs/AGENTS.md` §1
   taxonomy that the comment's request belongs to (e.g. `security/xss`,
   `quality/maintainability`, `performance/query`, `architecture/layering`,
   `standards/naming`). When a comment spans two, choose the one the *request*
   is about, not the one the code happens to sit in.
2. **`reviewer`** — determined by `category`: the owning agent key
   (`security` | `quality` | `performance` | `architecture` | `standards`).
   Not an independent judgement, and **not** the annotator's identity.
3. **`severity`** — impact the comment implies if unaddressed:
   | value | rule |
   | --- | --- |
   | `critical` | exploitable vulnerability, data loss, corruption, crash in production path |
   | `high` | correctness bug, broken behaviour, security-relevant defect |
   | `medium` | clear defect or maintainability problem with concrete cost |
   | `low` | minor improvement, missing test, small readability issue |
   | `info` | question, nit, style/typo with no functional effect |
4. **`confidence`** — annotator certainty in `category`+`severity`+`implementation`
   jointly: `0.9` clear, `0.75` mostly clear, `0.6` ambiguous, `0.4` unclear
   (kept only when the row is still includable).
5. **`implementation`** — compares the comment's *request* with `new` (the
   observed later commit, Li et al. §3.3, verified proposal V1):
   | value | rule |
   | --- | --- |
   | `verbatim` | the later change does what the comment asked, essentially as asked |
   | `partial` | addresses part of the request, or the same idea less completely |
   | `modified` | the code changed but not along the comment's request (different approach) |
   | *absent* | the later change does not address the comment **and** no explicit thread record settles it → row becomes `changed_unclassified`/excluded |
   Recorded **only** from the code pair + thread evidence (Auth.md evidence bar);
   never from "code changed" alone.

   **Rule H (partial hunks).** The archive's `old`/`new` regions can show only
   part of a commit. Judge only what is visible, at the comment's anchor:
   (a) visible change *is* the requested edit → `verbatim`;
   (b) visible change is one part of a multi-part request, a prerequisite for it
   (an added import/pointer needed before the real fix), or one half of a move
   (delete visible, insert outside the region) → `partial`;
   (c) same concern handled by a different approach → `modified`;
   (d) visible change unrelated to the request → *absent* (pass 2 checks the
   thread before the row is excluded). Where the request's remainder is forced
   by compilation (e.g. removing an abstract method makes children override it),
   (a) may still apply; otherwise prefer the weaker claim. Confidence is capped
   at `0.7` whenever Rule H (b) applies.
6. **`explicit_outcome`** — only from an explicit thread record in the fetched
   GitHub evidence (review/comment text saying accepted, dismissed, rejected,
   ignored). Absent otherwise. Never inferred from the change.
7. **`actionable`** — `true` when a concrete edit is requested or
   unambiguously implied; `false` for open questions/nits with no requested edit.
8. **`suggested_fix`** — copied **verbatim** from the comment when the comment
   itself carries a fix (a ` ```suggestion ` block or explicit replacement
   code); `null` otherwise. Never written by the annotator.

## Excluded-by-design outcomes (expected, not failures)

- `annotation_incomplete:file_path` — GitHub had no matching review comment
  (deleted repo, edited body, out-of-range match).
- `changed_unclassified` — change present, no implementation match and no
  explicit outcome (the corpus is filtered to comments that *were* followed by a
  change — proposal D3/V2, so this is the honest resting place for
  unresolvable rows).

## Reproducibility record

The pass writes `datasets/annotation/annotation_filled.jsonl` (judgements only,
evidence stays in `path_evidence.jsonl`), and the ingest manifest plus
`aid_manifest.json` record source sha256, seed, sample mode, this rubric's file
path, the annotator identity and the timestamp. Rows are annotated in file
order in batches; the batch files are kept so a re-run can diff.
