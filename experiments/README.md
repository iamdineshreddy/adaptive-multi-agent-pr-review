# Experiments

Reproducible experiment pipeline for ARUM evaluation (docs/EXPERIMENTS.md).

Layout:

```text
experiments/
    config/           baselines.json (budget/seed mirrors, source of truth in code)
    preprocessing/    preprocess.py  (raw JSONL -> labelled corpus + manifest)
    run/              run_all.py     (B1..B5 + A1..A6 -> results/)
    samples/          reviews_sample.jsonl (bundled smoke corpus)
    results/          CSV + JSON metrics (gitignored, reproduced locally)
```

All pipeline logic lives in `backend/app/experiments/` so the label rules, the
metric formulas, the mode switches and the selection runner are part of the
unit-tested gate (`tests/test_experiments_*.py`) — the CLI layers here are thin.

## How to run

Preprocess a raw corpus into the labelled research dataset:

```bash
python experiments/preprocessing/preprocess.py <input.jsonl> \
    --out labelled.jsonl --manifest manifest.json
```

Reproduce the full experiment table (defaults to the bundled sample):

```bash
python experiments/run/run_all.py [--corpus <raw.jsonl>] [--seed 0] \
    [--results-dir experiments/results]
```

Outputs: one reproducibility JSON per mode (`results/runs/<KEY>.json`),
a combined `summary.csv`, and `summary.json` with the ablation-vs-B5 delta table.
Every result payload carries its corpus provenance and an explicit
`honesty` block.

## Integrity contract

- **All results are pending experimental validation.** The bundled sample is a
  smoke corpus for pipeline verification, NOT research data; the paper's
  numbers await the `github-codereview` ingestion (datasets/README.md) and its
  licensing review.
- No invented numbers: every metric in a run payload was computed by an
  executed run over the exact listed corpus, with the seed and mode config
  recorded.
- Modes are switches in the same `app.adaptive` code the product runs — never a
  hand-wired alternate pipeline.