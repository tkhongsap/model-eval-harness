"""The production port becomes a constrained-decoding grammar. Three named deviations.

`schemas/retention.json` is a line-by-line port of production's `get_analysis_schema()`
(`main.py:954-1050`), and it stays that way so a reviewer can diff the two documents.
This module is the other half of that decision: the schema production *declares* and the
schema a provider is asked to *enforce* are not the same artifact, because the two
mechanisms are not the same mechanism.

    production   Vertex function declaration, `functionCallingConfig: {mode: ANY}`
                 (`main.py:1109-1114`), `maxOutputTokens: 65535` (`main.py:1120`).
                 ANY *strongly encourages* the function; the model still writes the
                 tokens it wants to write.
    here         OpenRouter `response_format: {"type": "json_schema", ...}`. The
                 provider CONSTRAINS every token to satisfy the grammar. A value the
                 grammar cannot express is not merely discouraged, it is unreachable.

Handing a declaration to a decoder without changing it produces measurement artefacts
that look like model behaviour. All three below were measured against the real models on
2026-08-04 at `temperature=0.0, top_p=0.0, seed=0, max_tokens=8000`, on items from
`tests/fixtures/testsets/retention_v1.jsonl`, and each is recorded with its before and
after rather than argued for.

**1. `network_issue` is removed from every product block.** It is an unbounded free-text
subtree — `sub_reason` is a bare `{"type": "string"}` and `problem_statement_list` a bare
array of them — sitting inside a grammar the decoder must satisfy. With it present,
BOTH arms degenerated on RET-01:

    google/gemini-2.5-flash   finish_reason=length, completion_tokens=8000, 23,529
                              chars, $0.02085. The output opens as a correct object and
                              then loops forever inside `network_issue.sub_reason`.
                              `outcomes.classify` filed it `refusal`, because the
                              repetition echoed `ขอโทษ` out of the transcript.
    qwen/qwen3.6-27b          returned `-1.1000000000000001e-05000000000...` — 524
                              tokens of a single degenerate number, which parses as
                              JSON and is therefore `schema_violation`.

    With the subtree removed, the same two requests returned `ok` at 180 tokens
    ($0.00130) and 2,290 tokens ($0.01046). Nothing downstream reads `network_issue`:
    `flatten.to_rows` consumes `retention_outcome` and `main`/`secondary`/`third`
    `.reason`, and nothing else. It stays in the prompt's worked example, so what the
    model is asked for is unchanged; only what the decoder is forced to produce is.

**2. `""` is added to every `reason` enum.** Every `reason.description` in the port says
"Use empty string if not applicable", the prompt says "If some fields can not be
determined, leave them empty string", and the ground truth has a blank `secondary` on
17 of its 22 rows. Under a constrained decoder the port made that instruction
impossible to obey: `""` is not in the enum, and `secondary`/`third` declare
`required: ["reason", "keyword"]`, so a model that entered the object had to invent a
label. After the fix, `qwen/qwen3.6-27b` returned `secondary=""` and `third=""` on
RET-01; before it, it could not.

    HONEST LIMIT, measured rather than assumed. This restores abstention; it does not
    stop fabrication, and the difference matters to anyone reading the reason
    dimension. On RET-06 (ground truth `main='post to pre'`, blank secondary and third)
    both arms still answered `secondary='save cost', third='dissatisfied service'`
    WITH the fix — and gemini answered `secondary='save cost', third='other'` with NO
    `response_format` at all. Those are the literal values in the prompt's worked
    example (`prompts/retention_wrapper.txt:24-31`); the models are copying the
    example, not obeying the grammar. That is a prompt finding, it costs the reason
    dimension real false positives on both arms, and it is NOT fixed here: editing a
    committed prompt asset changes what is being measured and belongs to whoever owns
    the eval, not to a schema module.

    RATE, so "does not stop fabrication" is a number rather than an impression. Over
    the first 8 items, both arms, at the standard decoding parameters, counting only
    the secondary/third slots the ground truth leaves blank (15 such slots per arm):

        google/gemini-2.5-flash   abstained on 8/15, fabricated 7  (rate 0.533)
        qwen/qwen3.6-27b          abstained on 9/15, fabricated 6  (rate 0.600)

    The deviation is therefore observable and load-bearing — before it, `""` was
    unreachable and the rate was 0/15 by construction — but roughly two fifths of the
    blank slots are still filled with a fabricated label on both arms. Read the reason
    dimension with that in front of you.

**3. `product` gets `minProperties: 1`.** An empty `product` object is schema-legal in
the port, and `outcomes.classify` checks that a required key is PRESENT, not that it
holds anything (`outcomes.py:344`). `flatten.to_rows` then emits the ground-truth
skeleton with `parse_ok=True`, and per `flatten.py`'s KNOWN CONSEQUENCE that arm scores
weighted product recall 1.000 while `Coverage.parse_failures` reads 0 — an arm that
named nothing is arithmetically indistinguishable from one that named everything
correctly. `minProperties` is a hint a decoder may or may not honour, so it is the
cheaper half of the fix: `flatten.named_no_product` is the half that counts the loss
whether or not the provider enforces this.

    MEASURED 2026-08-04, and it is NOT honoured on both arms. Asked point-blank for an
    empty product object, WITH this grammar attached, at `temperature=0.0, top_p=0.0,
    seed=0`:

        google/gemini-2.5-flash   `{"product": {}}` on 3 of 3 runs. `minProperties` was
                                  ignored outright; `outcomes.classify` reports `ok`.
        qwen/qwen3.6-27b          named a product on 2 of 3 (the third omitted `product`
                                  entirely and was correctly filed `schema_violation`).

    So on the incumbent arm this deviation buys nothing, and `named_no_product` is not
    a belt-and-braces second counter — it is the ONLY thing standing between "answered
    nothing" and a product recall of 1.000. Kept anyway because it costs one key and it
    does bind on the candidate, but it must not be described as the fix.

Applied structurally, not by a recursive walk, and every edit is counted. A blind walk
over "any dict with an `enum`" would also rewrite `retention_outcome`, `issue_type` and
`call_event_detection`, and would keep working silently if the port's shape moved
underneath it. `DEVIATIONS` names how many edits each change owes, and `decoding_schema`
raises when the count is wrong — the same discipline `prompts.AUDIO_TO_TRANSCRIPT` uses
for its literal repair pairs, for the same reason: an edit nobody can point at is worse
than no edit.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ABSTAIN",
    "DEVIATIONS",
    "DecodingSchemaError",
    "Deviation",
    "decoding_schema",
]

# The label a model writes when a reason slot does not apply. `records.norm_text` maps
# it to None and `parse_reasons` drops it, so it contributes nothing to a reason set --
# which is precisely the point: abstention must cost the model nothing, or it will
# guess instead.
ABSTAIN = ""

# The three scored reason slots (fact_checker.py:607-617). `keyword` is not scored and
# is left exactly as production declares it.
_REASON_SLOTS = ("main", "secondary", "third")

# The four product keys the port declares under `additionalProperties: false`
# (main.py:1035-1040). Named here so a port that gained or lost one fails loudly.
_PRODUCT_KEYS = ("Postpaid", "TOL", "TVS", "unknown")

# The unbounded free-text subtree. See deviation 1 in the module docstring.
_UNBOUNDED = "network_issue"


class DecodingSchemaError(RuntimeError):
    """The port no longer has the shape these deviations were written against.

    Raised rather than skipped. A deviation that silently stops applying leaves a
    grammar nobody reviewed in front of a paid run, and the run still produces numbers.
    """


@dataclass(frozen=True)
class Deviation:
    """One difference between the port and the grammar, with the count it owes."""

    name: str
    edits: int
    why: str


DEVIATIONS: tuple[Deviation, ...] = (
    Deviation(
        name="drop_network_issue",
        edits=len(_PRODUCT_KEYS),
        why=(
            "an unbounded free-text subtree inside a constrained grammar; measured to "
            "burn the whole 8000-token budget on gemini-2.5-flash and to emit a "
            "degenerate number on qwen3.6-27b, on the same item"
        ),
    ),
    Deviation(
        name="allow_abstention",
        edits=len(_PRODUCT_KEYS) * len(_REASON_SLOTS),
        why=(
            'the enum has no "" member, so the abstention every reason.description '
            "and the prompt both instruct is unreachable under enforcement"
        ),
    ),
    Deviation(
        name="require_a_product",
        edits=1,
        why=(
            "an empty product object is schema-legal, parses ok, and scores weighted "
            "product recall 1.000 with parse_failures 0"
        ),
    ),
)


def decoding_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The port, transformed into the grammar a provider is asked to enforce.

    Returns a deep copy. The caller reads the port from disk on every call and this
    function never touches it, so a mutation here cannot reach the file a reviewer
    diffs against `main.py`.

    Every deviation reports how many edits it made and is checked against
    `DEVIATIONS`. A count that does not match means the port moved, and the right
    answer is to re-derive the deviation against the new shape rather than to apply
    whatever part of it still happens to match.
    """
    out = copy.deepcopy(dict(schema))
    counts = {
        "drop_network_issue": 0,
        "allow_abstention": 0,
        "require_a_product": 0,
    }

    product = _node(out, ("properties", "product"))
    product["minProperties"] = 1
    counts["require_a_product"] += 1

    blocks = _node(product, ("properties",))
    missing = [key for key in _PRODUCT_KEYS if key not in blocks]
    if missing:
        raise DecodingSchemaError(
            f"the port no longer declares product key(s) {missing}. The deviations in "
            "this module are written against the four keys production declares under "
            "additionalProperties: false (main.py:1035-1040)."
        )

    for key in _PRODUCT_KEYS:
        block = _node(blocks, (key, "properties"))
        if block.pop(_UNBOUNDED, None) is not None:
            counts["drop_network_issue"] += 1
        for slot in _REASON_SLOTS:
            enum = _node(block, (slot, "properties", "reason")).get("enum")
            if not isinstance(enum, list):
                raise DecodingSchemaError(
                    f"product.{key}.{slot}.reason has no enum to extend. Abstention is "
                    "only unreachable because the enum is closed; without one this "
                    "deviation has nothing to fix and must not report that it did."
                )
            if ABSTAIN not in enum:
                enum.append(ABSTAIN)
                counts["allow_abstention"] += 1

    for deviation in DEVIATIONS:
        actual = counts[deviation.name]
        if actual != deviation.edits:
            raise DecodingSchemaError(
                f"deviation {deviation.name!r} made {actual} edit(s), not "
                f"{deviation.edits}. It exists because {deviation.why}. A deviation "
                "that half-applies leaves a grammar nobody reviewed in front of a paid "
                "run."
            )
    return out


def _node(root: Mapping[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    """Walk to a nested object, naming the exact step that was missing.

    `KeyError: 'properties'` from four levels down is unreadable, and the reader's next
    question is always which of the four.
    """
    node: Any = root
    for index, key in enumerate(path):
        if not isinstance(node, dict) or key not in node:
            raise DecodingSchemaError(
                f"the port has no {'.'.join(path[:index + 1])}. "
                "schemas/retention.json is the port of main.py:954-1050 and these "
                "deviations are written against that shape."
            )
        node = node[key]
    if not isinstance(node, dict):
        raise DecodingSchemaError(
            f"{'.'.join(path)} is {type(node).__name__}, not an object."
        )
    return node
