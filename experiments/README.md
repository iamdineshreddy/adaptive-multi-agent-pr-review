# Experiments

Reproducible experiment pipeline for ARUM evaluation.

Planned layout (Phase 17):

```text
experiments/
    config/           baseline + ablation definitions
    preprocessing/    dataset download/cleaning + label mapping
    run/              run_all script (baselines B1..B5 + ablations)
    results/          CSV + JSON metrics + plots (gitignored)
    notebooks/        analysis/figures for the paper
```

**All results are pending experimental validation.** No metrics reported here until
`experiments/run/run_all` has produced them.