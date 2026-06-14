"""Reusable, individually-tested pipeline stages.

Each stage is a self-contained capability shared across the CRE / CR /
designer pipelines, built as: a deterministic Python engine that owns
correctness (fully unit-tested) plus, where genuine judgment is needed,
an optional bounded LLM layer on top (a separate function, tested against
the REAL LLM — never mocked). Canonical component types come from PEAS
(``heaviside.types``). See ``docs/stages_refactor_roadmap.md``.
"""
