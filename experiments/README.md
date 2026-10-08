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
Every result payload carries its corpus provenance, the code commit/dirty flag
(`code_version`), and an explicit `honesty` block; `summary.json` additionally
records the execution timestamp and interpreter version (wall-clock values live
there only, so per-mode payloads stay deterministic for a fixed seed).

## Integrity contract

- **The real run is executed; measured and unmeasured quantities are labelled
  separately.** The bundled sample remains a smoke corpus for pipeline
  verification, NOT research data. The research run executed over the
  annotated `github-codereview` corpus (Li et al. CodeReview archive, Zenodo,
  CC-BY-4.0 — `datasets/README.md`); its results table is in
  `docs/EXPERIMENTS.md` §7. Quantities no harness output measures (latency,
  tokens, cost, queue throughput, NDCG/MRR, B1) stay explicitly "NOT
  MEASURED" there — never estimated.
- No invented numbers: every metric in a run payload was computed by an
  executed run over the exact listed corpus, with the seed and mode config
  recorded.
- Modes are switches in the same `app.adaptive` code the product runs — never a
  hand-wired alternate pipeline.