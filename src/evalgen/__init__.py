"""Synthetic Thai test-set generation for the Retention evaluation.

The model-calling half of the repository, kept in its own package because
`evalharness` must stay free of clients: `evalgen` generates, `evalharness` scores,
and `tests/test_boundary.py` enforces that the dependency never runs the other way.
"""
