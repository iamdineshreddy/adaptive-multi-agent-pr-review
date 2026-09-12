# Adaptive Review Utility Model (ARUM)

**Working name.** The name may change as the research matures.

ARUM is the decision mechanism that answers the second core question of the project:

> Given a set of detected findings, which ones should actually be shown to the
> developer, in what order, and with what review budget?

ARUM is deliberately **not** an LLM ranking prompt. It is a measurable scoring model
whose coefficients are configurable, trainable, and experimentally evaluated.

---

## 1. Conceptual formulation

For a candidate finding `f` (already grouped by redundancy) the review utility is:

```text
Utility(f) =
      w1 · Severity(f)
    + w2 · Confidence(f)
    + w3 · AgentAgreement(f)
    + w4 · HistoricalActionability(f)
    + w5 · RepositoryRelevance(f)
    + w6 · ContextRelevance(f)
    - w7 · Redundancy(f)
    - w8 · HistoricalRejection(f)
```

Each term is normalised to `[0, 1]` so weights are comparable and interpretable.

**No weight is assumed optimal.** Weights start from a documented heuristic prior
(policy file), then are updated and evaluated with the learning module.

---

## 2. Feature definitions

| Feature | Source | Definition / normalisation |
| --- | --- | --- |
| `Severity` | Agent verdict | Ordinal scale MAPPED: critical=1.0, high=0.8, medium=0.5, low=0.2, info=0.05. Calibrated per category from history. |
| `Confidence` | Agent verdict | 0..1 from the finding `confidence` field. |
| `AgentAgreement` | Consolidation | 1.0 if two+ agents produced semantically identical findings; 0.5 single agent corroborated by evidence; falls as agents disagree (see §8). |
| `HistoricalActionability` | Memory (per repo + category) | Rolling share of findings in this category that were `ACCEPTED`/`FIXED` in the last T days, with temporal decay applied. |
| `RepositoryRelevance` | Memory / RAG | Semantic similarity of this finding to the repository's accepted concerns and coded standards (pgvector cosine). |
| `ContextRelevance` | RAG + diff | Similarity to previously resolved findings in the same file path/component; penalises re-raising already-closed issues unless new evidence exists. |
| `Redundancy` (negative) | Redundancy layer | `1 - 1/group_size` — the more duplicates collapsed into this group, the smaller the multiplier on the term (only the top group candidate publishes). |
| `HistoricalRejection` (negative) | Memory | Rolling share of similar findings `REJECTED`/`DISMISSED` in the same category, temporally decayed. |

All rolling statistics derive from the `developer_feedback` and `repository_memory`
tables (see `docs/DATABASE.md`).

---

## 3. Configurable weights

Weights are stored per deployment (global defaults) and per repository (fine-tune
overrides):

```json
{
  "v1": {
    "w1_severity": 0.30,
    "w2_confidence": 0.20,
    "w3_agent_agreement": 0.15,
    "w4_historical_actionability": 0.15,
    "w5_repository_relevance": 0.10,
    "w6_context_relevance": 0.10,
    "w7_redundancy": 0.08,
    "w8_historical_rejection": 0.10
  }
}
```

The initial values are a documented heuristic prior; they are **calibrated during the
experiment phase**, not hard-coded as if optimal. Versioning (`v1`, `v2`, …) lets the
system label the model family used for each published review (reproducibility).

---

## 4. Learning approaches evaluated

We evaluate, in order of expected fit:

1. **Logistic regression / scalar relevance calibration** — simplest baseline that maps
   features to a binary "publish / suppress" probability. Interpretable coefficients,
   low data requirements. Minimum viable adaptive mechanism.
2. **Gradient-boosted trees (LightGBM)** — captures feature interactions (e.g.
   severity×category, repo×category) where linear models are weak.
3. **Learning-to-rank (LTR)** — directly optimises review-pair or listwise loss
   (`publish one finding ahead of another`). Strong match for the "which findings to
   show first" problem; needs pairwise preference data derived from feedback.
4. **Contextual bandits (e.g. LinUCB)** — only if we treat "should we publish this
   finding *now*, given repo state" as a sequential decision problem. Higher value if
   we want exploration of suppressed findings to measure hidden actionability.

**Explicit choice.** We start with **gradient-boosted trees (LightGBM)** as primary
with a logistic-regression ablation baseline, because: (a) tabular features, (b) strong
interaction modelling, (c) interpretable SHAP attribution for the research write-up,
(d) low implementation risk, (e) simple preference-data generation from feedback. LTR
is registered as a future extension that a preference dataset enables. Bandit-based
exploration is deferred behind a documented safety gate (it is unsuitable for
*suppression* decisions on critical findings without strong guardrails).

The choice is recorded and revisited after Phase 17 results — **not** defended as
permanently optimal.

---

## 5. Subjective-quality measurement targets

ARUM's effectiveness is not judged by "LLM agrees with us". It is judged by developer
outcomes and system metrics (all logged):

| Metric | Definition |
| --- | --- |
| Precision | Published findings with `ACCEPTED`/`FIXED` outcome ÷ published |
| Recall | Detected issues that were *both* real and published ÷ all real issues (evidence: feedback outcomes + label audit) |
| F1 | Harmonic mean of the above over a labelled audit subset |
| False positive rate | Suppressed-or-rejected non-issues ÷ non-issues |
| Comment reduction | (raw findings − published findings) ÷ raw findings |
| Redundancy reduction | duplicates collapsed ÷ raw findings |
| Developer acceptance rate | `ACCEPTED` + `FIXED` ÷ all outcomes |
| Actionability | Share of published findings with root-caused, actionable suggestions |
| Latency / token / cost | per review, per agent |

See `docs/EXPERIMENTS.md` for baselines and ablation design.

---

## 6. Temporal decay (memory freshness)

Rolling histories (actionability, rejection) are weighted with exponential decay over
time so old decisions fade:

```text
weight(t) = exp(-λ · (now − t) / τ)
```

- `τ` default = 90 days (configurable per repository).
- `λ` default = 1.0 (configurable).
- Findings older than `3·τ` have negligible influence.

That means a repository which abolished a coding rule 4 months ago stops being
penalised for following it today. Decay parameters and the rationale are documented in
`docs/EXPERIMENTS.md` (ablation: "no feedback memory").

---

## 7. Adaptive review budget

Budget is a cap on published findings per review, based on the PR risk class computed
at priority time:

| Risk class | Suggested cap |
| --- | --- |
| low | 5 |
| medium | 10 |
| high | 20 |

Cap values are configurable (`review_budget` settings). Selection fills the budget by
ARUM rank.

### Safety gates (non-negotiable, configurable policy)

- **critical severity** findings → mandatory publication unless explicitly suppressed
  by a human-admin rule.
- **high confidence AND high severity** → protected from budget truncation.
- **low confidence AND low severity AND high redundancy** → candidate for suppression
  (most aggressive).

The gates are a hard policy layer evaluated *after* ARUM, never bypassed silently; all
gated decisions are logged with their trigger reason.

---

## 8. Agent disagreement handling

Disagreement = high variance of severity/confidence across agents for the same
underlying finding (after redundancy grouping). If variance exceeds a threshold:

- the finding is escalated to Supervisor verification (a dedicated,
  evidence-restricted follow-up), **or**
- review priority is bumped and the finding is routed for additional verification,
- a `disagreement` metric is recorded so the mechanism itself can be evaluated.

When agents disagree, `AgentAgreement(f)` is reduced and the finding must rely on
severity/confidence evidence to remain in the publication set — preventing "one
confident agent" from gaining weight relative to consensus.

---

## 9. Iterative review scoring

On a re-review (new commit), ARUM re-evaluates only affected findings:

- Previously published, still valid, code untouched → re-publish with
  `historical_context = previous round`, lower cost.
- Previously published, now `RESOLVED`/gone → suppress; record positive outcome
  evidence if the change matches the suggestion.
- Previously suppressed, still present, and **new evidence** (new commits) changed
  features → re-score and possibly publish.

This makes round 2 cheaper and quieter (see `docs/EXPERIMENTS.md` for the
efficiency measurements: tokens saved, agents invoked, repeated findings avoided).

---

## 10. Reproducibility contract

- Every published/ suppressed decision stores: ARUM version, all eight feature values,
  weights used, budget, safety-gate outcome, and the redundancy group id.
- Rerunning a review with the same inputs and model version reproduces the same decision.
- Experiments log feature distributions and decision traces to CSV/JSON for analysis.