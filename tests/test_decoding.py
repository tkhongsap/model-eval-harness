"""The port and the grammar, checked against each other.

Two claims are defended here and they pull in opposite directions, which is why the
transformation exists as its own function rather than as three edits to a JSON file:

  * `schemas/retention.json` is still the PORT. It must keep saying what
    `main.py:954-1050` says, so a reviewer can diff the two. Every test in the first
    section asserts a defect is STILL THERE on disk.
  * `decoding_schema` is the grammar. Every test in the second section asserts the same
    defect is gone from what gets sent.

A test that only checked the second half would pass just as happily if someone had
edited the port, and the repository would have lost the artifact it uses to prove it
reproduces production.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.decoding import (  # noqa: E402
    ABSTAIN,
    DEVIATIONS,
    DecodingSchemaError,
    decoding_schema,
)

SCHEMA_PATH = ROOT / "src" / "evalgen" / "schemas" / "retention.json"
PRODUCT_KEYS = ("Postpaid", "TOL", "TVS", "unknown")
REASON_SLOTS = ("main", "secondary", "third")


@pytest.fixture(scope="module")
def port() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def grammar(port: dict) -> dict:
    return decoding_schema(port)


def _blocks(schema: dict) -> dict:
    return schema["properties"]["product"]["properties"]


# --- the port keeps production's shape, defects included ------------------------------


def test_the_port_still_declares_network_issue(port: dict) -> None:
    """Production declares it (main.py:981-1006) and so must the port.

    Deviation 1 removes it from the grammar because an unbounded free-text subtree
    inside a constrained decoder made both arms degenerate. That is a statement about
    OUR decoding path, not a claim that production's schema lacks the field, and the
    file a reviewer diffs has to keep saying what production says.
    """
    for key in PRODUCT_KEYS:
        assert "network_issue" in _blocks(port)[key]["properties"], key


def test_the_port_still_has_no_abstention_in_the_reason_enum(port: dict) -> None:
    """The defect deviation 2 exists to work around, asserted where it lives.

    Production's enum has eleven labels and no empty string (main.py:970), while the
    description in the same object instructs "Use empty string if not applicable". If
    this test starts failing, someone edited the port, and deviation 2's edit count
    will refuse the next run -- which is the intended order of events.
    """
    for key in PRODUCT_KEYS:
        for slot in REASON_SLOTS:
            enum = _blocks(port)[key]["properties"][slot]["properties"]["reason"]["enum"]
            assert ABSTAIN not in enum, f"{key}.{slot}"
            assert "Use empty string if not applicable" in (
                _blocks(port)[key]["properties"][slot]["properties"]["reason"]["description"]
            )


def test_the_port_lets_product_be_empty(port: dict) -> None:
    """`minProperties` is absent, so `{"product": {}}` is schema-legal in production."""
    assert "minProperties" not in port["properties"]["product"]


# --- the grammar fixes all three, and counts the edits --------------------------------


def test_the_grammar_drops_every_network_issue_subtree(grammar: dict) -> None:
    """MEASURED: with it present, gemini-2.5-flash burned all 8000 tokens on RET-01
    (23,529 chars, $0.02085, finish_reason=length) and qwen3.6-27b returned 524 tokens
    of `-1.1000000000000001e-05000...`. Without it, both returned `ok`.

    Nothing downstream reads the field: `flatten.to_rows` consumes `retention_outcome`
    and `main`/`secondary`/`third`.`reason`, and that is the whole list.
    """
    for key in PRODUCT_KEYS:
        assert "network_issue" not in _blocks(grammar)[key]["properties"], key


def test_the_grammar_lets_a_model_abstain_on_every_reason_slot(grammar: dict) -> None:
    """All twelve, not just `secondary` and `third`.

    `main` is included because the enum is one shared object in production
    (`PRODUCT_REASON_SCHEMA`, main.py:966-980) and a grammar that let a model abstain
    on two of three slots would be a third rule nobody wrote down.
    """
    for key in PRODUCT_KEYS:
        for slot in REASON_SLOTS:
            enum = _blocks(grammar)[key]["properties"][slot]["properties"]["reason"]["enum"]
            assert ABSTAIN in enum, f"{key}.{slot} cannot express abstention"
            assert len(enum) == 12, f"{key}.{slot} enum is {len(enum)} long, expected 11 + ''"


def test_the_grammar_refuses_an_empty_product_object(grammar: dict) -> None:
    """The half of the empty-answer fix that lives in the schema.

    The other half is `flatten.named_no_product`, because `minProperties` is a hint a
    decoder may or may not honour, and the count has to hold either way.
    """
    assert grammar["properties"]["product"]["minProperties"] == 1


def test_the_grammar_changes_nothing_else(port: dict, grammar: dict) -> None:
    """Everything a score depends on survives the transformation untouched.

    The three deviations are surgical by construction; this is the assertion that says
    so from the outside, so a future fourth edit cannot arrive unannounced inside one
    of them.
    """
    assert grammar["required"] == port["required"] == [
        "product", "call_event_detection", "recommendation",
    ]
    assert grammar["properties"]["product"]["additionalProperties"] is False
    assert list(_blocks(grammar)) == list(PRODUCT_KEYS)
    assert (
        grammar["properties"]["call_event_detection"]
        == port["properties"]["call_event_detection"]
    )
    for key in PRODUCT_KEYS:
        block, original = _blocks(grammar)[key], _blocks(port)[key]
        assert block["required"] == original["required"] == ["main", "retention_outcome"]
        assert (
            block["properties"]["retention_outcome"]
            == original["properties"]["retention_outcome"]
        )
        for slot in REASON_SLOTS:
            assert (
                block["properties"][slot]["properties"]["keyword"]
                == original["properties"][slot]["properties"]["keyword"]
            )


def test_the_port_on_disk_is_not_mutated(port: dict) -> None:
    """`decoding_schema` deep-copies. A shared nested dict would edit the reviewer's
    artifact in memory and the second call would report a different edit count."""
    decoding_schema(port)
    decoding_schema(port)  # would raise on the second pass if the first mutated it
    for key in PRODUCT_KEYS:
        assert "network_issue" in _blocks(port)[key]["properties"]


# --- the edit counts are the guard ----------------------------------------------------


def test_every_deviation_declares_the_number_of_edits_it_owes() -> None:
    """4 product blocks, 12 reason slots, 1 product object. Stated, not derived."""
    assert {d.name: d.edits for d in DEVIATIONS} == {
        "drop_network_issue": 4,
        "allow_abstention": 12,
        "require_a_product": 1,
    }


def test_a_port_that_moved_is_refused_rather_than_half_transformed(port: dict) -> None:
    """The failure this file exists to make loud.

    A deviation that silently applies to three of four product blocks leaves one
    unbounded free-text subtree in a grammar nobody reviewed, in front of a paid run,
    and the run still produces numbers.
    """
    import copy

    damaged = copy.deepcopy(port)
    del damaged["properties"]["product"]["properties"]["TVS"]["properties"]["network_issue"]
    with pytest.raises(DecodingSchemaError) as exc:
        decoding_schema(damaged)
    assert "drop_network_issue" in str(exc.value) and "3 edit(s)" in str(exc.value)


def test_a_missing_product_key_is_named(port: dict) -> None:
    import copy

    damaged = copy.deepcopy(port)
    del damaged["properties"]["product"]["properties"]["TOL"]
    with pytest.raises(DecodingSchemaError) as exc:
        decoding_schema(damaged)
    assert "TOL" in str(exc.value)


def test_a_reason_slot_that_lost_its_enum_is_refused(port: dict) -> None:
    """Abstention is only unreachable because the enum is closed. With no enum there is
    nothing to fix, and reporting an edit that did nothing is worse than refusing."""
    import copy

    damaged = copy.deepcopy(port)
    del damaged["properties"]["product"]["properties"]["Postpaid"]["properties"]["main"][
        "properties"
    ]["reason"]["enum"]
    with pytest.raises(DecodingSchemaError) as exc:
        decoding_schema(damaged)
    assert "Postpaid.main" in str(exc.value)


def test_the_cli_sends_the_grammar_and_not_the_port() -> None:
    """The wiring, asserted where a reviewer would look for it.

    `cli.response_format` is the only path from this module to a request, and
    `runner._required_keys` unwraps exactly the shape it returns.
    """
    from evalgen.cli import response_format

    sent = response_format()["json_schema"]["schema"]
    assert sent["properties"]["product"]["minProperties"] == 1
    assert "network_issue" not in sent["properties"]["product"]["properties"]["Postpaid"]["properties"]
    assert tuple(sent["required"]) == ("product", "call_event_detection", "recommendation")
