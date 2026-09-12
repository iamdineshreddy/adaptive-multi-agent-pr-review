# Datasets

Dataset registration, preprocessing, and licensing notes.

## Planned

- **Primary: GitHub CodeReview** (`github-codereview`) — code context, review comments,
  before/after info, negative examples. Label mapping documented in
  `docs/EXPERIMENTS.md`.

## Policies

- No dataset is added without a documented license and provenance.
- Preprocessing scripts live in `experiments/preprocessing/` and are reproducible.
- Raw data is not committed to the repo; scripts download and cache into a
  git-ignored location (path defined in config).