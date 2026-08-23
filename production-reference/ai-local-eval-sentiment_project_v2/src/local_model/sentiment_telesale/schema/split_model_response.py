"""The fan-out registry: which model to decode into, under which prompt file.

The local telesale pipeline never asks for the whole answer in one request. The production
prompt is 162 KB and the local endpoint slows sharply on a long context, so each call carries
one hand-trimmed topic prompt and decodes into one sub-model of
:class:`~src.local_model.sentiment_telesale.schema.model_response.ModelResponse`.

Four sections, one per scored family, so every request maps onto a block of the ground-truth
sheet:

===================  ======================  ===============================
``key``              ``model``               ``prompt_file``
===================  ======================  ===============================
operations           OperationsSection       operations_prompt.txt
sales_effectiveness  SalesEffectiveness      sales_effectiveness_prompt.txt
customer_experience  CustomerExperience      customer_experience_prompt.txt
compliance           Compliance              compliance_prompt.txt
===================  ======================  ===============================

Two things differ from the QA sibling
(:mod:`src.local_model.sentiment.schema.split_model_response`) and both are deliberate:

* **Prompts are separate files, not runtime slices of one.** QA cuts its monolith on a header
  regex, which yields the same per-request size but the same per-request *text*. These four are
  hand-trimmed -- the campaign-ratio worked examples, the ``support_detail`` rules and the
  seven-scenario verification walkthrough are all dropped, because nothing in this contract
  generates the fields they describe. That trades comparability with the google prompt for
  roughly a fifth of the context, which is the trade this endpoint needs.
* **``key`` is not always the merge destination.** ``operations``'s dump spreads across the
  merged top level, because :class:`OperationsSection` carries ``call_status`` beside the
  operations block; the other three write a single top-level field named by ``key``.
  :data:`SPREAD_KEYS` names the first kind so the merge does not have to special-case by
  string literal.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.local_model.sentiment_telesale.schema.model_response import (
    Compliance,
    CustomerExperience,
    OperationsSection,
    SalesEffectiveness,
    _SchemaModel,
)


@dataclass(frozen=True)
class Section:
    """One fan-out unit: which model to decode into, under which prompt file.

    ``key`` names the merged-dict destination and the ``Error Type`` prefix a failure carries,
    so it must stay stable -- a resumed run matches checkpoints on it.
    """

    key: str
    model: type[_SchemaModel]
    prompt_file: str


SECTIONS: tuple[Section, ...] = (
    Section("operations", OperationsSection, "operations_prompt.txt"),
    Section("sales_effectiveness", SalesEffectiveness, "sales_effectiveness_prompt.txt"),
    Section("customer_experience", CustomerExperience, "customer_experience_prompt.txt"),
    Section("compliance", Compliance, "compliance_prompt.txt"),
)

#: Sections whose dump spreads across the merged top level instead of nesting under ``key``.
SPREAD_KEYS: frozenset[str] = frozenset({"operations"})

#: Every prompt file a run must find before it spends anything. Derived from SECTIONS, so
#: adding a section without writing its prompt aborts the run instead of failing per file.
EXPECTED_PROMPT_FILES: tuple[str, ...] = tuple(section.prompt_file for section in SECTIONS)


__all__ = ["EXPECTED_PROMPT_FILES", "SECTIONS", "SPREAD_KEYS", "Section"]
