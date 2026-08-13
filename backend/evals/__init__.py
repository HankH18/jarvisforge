"""Grounding eval harness (T-7; SPEC R6/R6a).

Scores the REAL ``jarvis.llm.synthesis.synthesize_grounded`` against an
approved golden set of grounding questions, so semantic refusal/answer
judgment -- the part ``synthesize_grounded`` deliberately leaves to the
model (see that module's docstring) -- has an external, machine-checkable
proxy. See ``backend/evals/README.md`` for how to run it.
"""

from __future__ import annotations
