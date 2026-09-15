"""Experiments harness: reproducible ARUM evaluation (docs/EXPERIMENTS.md).

The experiment pipeline reuses the production selection machinery
(``app.adaptive``) rather than re-implementing the pipeline, so baselines and
ablations measure the *same code* the product runs. Nothing here reports a
number that was not produced by an executed run over a concrete corpus.
"""
