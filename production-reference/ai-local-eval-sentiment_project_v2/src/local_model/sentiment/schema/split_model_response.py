"""Per-topic response models for the split analysis pipeline.

``internal_asr_llm_split_output.py`` issues one structured-output call per top-level
topic of :class:`~src.local_model.sentiment.schema.model_response.ModelResponse`
instead of one call for the whole model, then merges the parsed pieces back into a
single ``ModelResponse``. The six category sub-models are reused directly -- they
already subclass ``_SchemaModel`` and carry their own field order and aliases -- so
this module only adds the one piece ``ModelResponse`` does not expose as a sub-model:
the three top-level scalars, wrapped here as :class:`CallClassification`.

:data:`SECTIONS` is the registry the pipeline fans out over. Order is the merge
order, mirroring ``ModelResponse``'s field declaration order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from src.local_model.sentiment.schema.model_response import (
    CustomerExperience,
    CustomerInsight,
    CustomerSentiment,
    Network,
    SaleOpportunity,
    ServiceQuality,
    _SchemaModel,
)


class CallClassification(_SchemaModel):
    """The three top-level ``ModelResponse`` fields, as their own small call.

    ``call_type`` must stay annotation-identical to ``ModelResponse.call_type`` --
    the merged dict is re-validated by ``ModelResponse``, whose ``mode="before"``
    validator accepts the list this model dumps. Deliberately **no** serializer
    here: ``ModelResponse`` owns the list -> comma-string serialization, and a
    second one would hand it a pre-joined string instead of the parsed list.
    """

    service_number: str
    call_type: list[Literal["Enquiry", "Service Request", "Complaint", "Sale", "Retention"]] = Field(
        description="1.Complaint, 2.Retention, 3.Service Request, 4.Enquiry, 5.Sale"
    )
    call_type_confident: str


@dataclass(frozen=True)
class Section:
    """One fan-out unit: which model to decode into, under which prompt slice.

    ``key`` is the merged-dict destination -- the ``ModelResponse`` field name,
    except ``"classification"``, whose dump spreads across the top level.
    ``prompt_key`` names the ``### **Category N.: `<key>`**`` slice of the system
    prompt this call rides with; ``None`` means the shared preamble alone.
    """

    key: str
    model: type[_SchemaModel]
    prompt_key: str | None


SECTIONS: tuple[Section, ...] = (
    Section("classification", CallClassification, None),
    Section("customer_insight", CustomerInsight, "customer_insight"),
    Section("service_quality", ServiceQuality, "service_quality"),
    Section("sale_opportunity", SaleOpportunity, "sale_opportunity"),
    Section("customer_sentiment", CustomerSentiment, "customer_sentiment"),
    Section("customer_experience", CustomerExperience, "customer_experience"),
    Section("network", Network, "network"),
)

# The prompt-section names every sliced system prompt must contain -- the run
# aborts before any spend when one is missing from either direction's prompt.
EXPECTED_PROMPT_SECTIONS: frozenset[str] = frozenset(
    section.prompt_key for section in SECTIONS if section.prompt_key is not None
)

__all__ = ["CallClassification", "Section", "SECTIONS", "EXPECTED_PROMPT_SECTIONS"]
