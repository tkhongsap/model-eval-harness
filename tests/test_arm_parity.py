"""Every arm sends the same request, and the differences that remain are the documented ones.

WHY THIS FILE EXISTS. "We ran all models identically" is the sentence a cross-model comparison
rests on, and until now it was an assertion nobody could check. Auditing it by hand on
2026-08-21 turned up four differences, none of them visible in any run log:

  1. The audio arm sent an extra `{"type": "text", "text": "."}` part ahead of the audio --
     one token, on one arm, that no document mentioned.
  2. Neither OpenRouter arm sent `provider.require_parameters`. `request.py:22-26` spells out
     what that costs: "OpenRouter DROPS a parameter an endpoint does not support rather than
     failing, so without it an arm rerouted mid-run to an endpoint with no structured-output
     support falls back to prompt-coaxing, and the run log looks identical."
  3. Neither sent `reasoning.effort`, so Gemini ran at provider default while
     `retention-e23.plan.json:311` preregisters `"none"`.
  4. No arm recorded who actually answered -- only the model we ASKED for. The main harness
     captures `observed_model` / `provider` / `finish_reason` (`client.py:291-310`) because a
     2026-08-04 run was silently served by two backends under one model id.

Worth stating plainly: (2) was checked against the data before being called a bug, and 414 of
414 Gemini calls parsed with zero out-of-enum values. The guard was not rescuing a broken run.
It is here so that stays a checked fact rather than a lucky one.

These tests read the payload the runner would actually build, so they fail when the requests
diverge -- not when someone forgets to update a comment.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
E21_PATH = REPO / "scripts" / "experiment21_pipeline_delta.py"
RAW = E21_PATH.read_text(encoding="utf-8")


def _code_only(source: str) -> str:
    """The source with comments and docstrings removed.

    These tests assert on what the runner SENDS, and the first draft of this file failed
    against its own explanatory prose: the comment recording that the framing `"."` part had
    been removed contains the literal `"text": "."`, so a naive substring search found the
    thing it was checking was gone. Counting `response_format()` hit the same problem from the
    other side and matched the function's own `def` line.

    A test that a comment can break is a test that will be "fixed" by weakening it.
    """
    import io
    import tokenize

    out, last_end = [], (1, 0)
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    prev_type = None
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            prev_type = tok.type
            continue
        # A string that is the entire logical line is a docstring.
        if (tok.type == tokenize.STRING
                and prev_type in (tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, None)):
            prev_type = tok.type
            continue
        if tok.start[0] != last_end[0]:
            out.append("\n")
        out.append(tok.string)
        last_end = tok.end
        if tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT):
            prev_type = tok.type
    return " ".join(out)


SOURCE = _code_only(RAW)

TEXT_ARMS = ("ceiling", "format-control", "qwen-pipeline", "typhoon-pipeline",
             "gemini-asr-text")
OPENROUTER_ARMS = ("gemini-audio", "ceiling-gemini")


def test_the_audio_arm_sends_no_framing_text_part():
    """One token, on one arm, that nobody reviewed.

    `prompts.build_messages` states the principle it broke: any word added around the input
    is a word this harness invented and then scored a model on.
    """
    assert '"text" : "."' not in SOURCE, (
        "the audio arm is sending a framing text part again. Every other arm sends the input "
        "and nothing else."
    )


@pytest.mark.parametrize("guard,why", [
    ('"provider" : { "require_parameters" : True }',
     "without it OpenRouter silently drops response_format and the log looks identical"),
    ('"reasoning" : { "effort" : "none" }',
     "without it Gemini runs at provider default, contradicting the preregistered plan"),
    ('"usage" : { "include" : True }',
     "without it the record carries no cost for the OpenRouter arms"),
])
def test_the_openrouter_guards_are_sent(guard, why):
    assert guard in SOURCE, f"missing OpenRouter guard {guard}: {why}"


def test_the_guards_are_on_the_openrouter_branch_only():
    """Token Factory REJECTS these -- `runtime.py:402-414` raises for OPENAI_COMPATIBLE.

    So this is not a case of "send them everywhere to be safe": sending them to the internal
    gateway would break the five text arms.
    """
    branch = SOURCE[SOURCE.index('if arm in ( "gemini-audio" , "ceiling-gemini" )'):]
    tf_start = branch.index("model = QWEN_LABELLER")
    openrouter, token_factory = branch[:tf_start], branch[tf_start:tf_start + 900]
    for guard in ("require_parameters", '"reasoning"', '"usage"'):
        assert guard in openrouter, f"{guard} is not on the OpenRouter branch"
        assert guard not in token_factory, (
            f"{guard} is being sent to Token Factory, which rejects it "
            "(runtime.py:402-414)"
        )


def test_every_arm_shares_one_decoding_literal():
    """Not "the same values" -- the same object, spread into both payloads.

    Two dicts that happen to match today are two dicts that can stop matching.
    """
    assert SOURCE.count("** DECODING") == 2, (
        f"DECODING is spread {SOURCE.count('** DECODING')} times; expected exactly 2 -- one "
        "per runtime branch. A third payload means an arm with its own decoding."
    )
    assert RAW.count("DECODING = {") == 1, "more than one DECODING definition"


def test_every_arm_shares_one_schema_call():
    """`response_format()` is one function over one committed file, called per payload."""
    call_sites = SOURCE.count("response_format ( )") - SOURCE.count(
        "def response_format ( )")
    assert call_sites == 2, (
        f"response_format() is called from {call_sites} payload sites; expected 2, one "
        "per runtime branch. An arm may be sending a different schema."
    )


def test_every_arm_uses_the_same_message_layout():
    """system = the prompt, user = the input. Three sites, identical shape."""
    assert SOURCE.count('{ "role" : "system" , "content" : system }') == 3, (
        "the system turn is not identical across the three payload sites"
    )


def test_only_the_audio_arm_differs_in_user_content():
    """The audio arm sends a content LIST; every text arm sends a bare string.

    That difference is intrinsic -- audio cannot be a string -- and is the only one allowed.
    """
    assert SOURCE.count('{ "role" : "user" , "content" : user_text }') == 2
    assert SOURCE.count('{ "role" : "user" , "content" : content }') == 1


def test_the_run_records_who_actually_answered():
    """Asking for a model is not the same as being served by it."""
    for field in ("observed_model", "provider", "finish_reason"):
        assert f'rec [ "{field}" ]' in SOURCE, (
            f"the record no longer captures {field}; model identity becomes unverifiable, "
            "which is the exact failure runtime_gate was built for"
        )


def test_the_audio_prompt_differs_only_by_the_six_documented_pairs():
    """The one text difference between the audio arm and the text arms, end to end.

    Reimplemented here from `evalgen.prompts` rather than imported from the runner, for two
    reasons. The runner imports `httpx` at module scope because it calls models, and
    `requirements.txt` pins production's scoring dependencies and contains no HTTP client --
    so importing it passes locally and fails collection on CI, which is exactly how
    `test_arm_wiring.py` broke once already.

    The better reason: a test that reproduces the reversal INDEPENDENTLY is checking the
    substitution set, while a test that calls `audio_prompt_text()` is checking that a
    function agrees with itself.
    """
    import hashlib

    from evalgen import prompts as P

    text = P.PROMPTS["v9_16_base"].system_text

    audio = text
    for before, after in P.AUDIO_TO_TRANSCRIPT:
        assert audio.count(after) == 1, (
            f"{after[:44]!r} appears {audio.count(after)} times in the text prompt; the "
            "reversal would be partial and nobody would have reviewed the result"
        )
        audio = audio.replace(after, before, 1)

    assert audio != text, "the six substitutions changed nothing"

    rebuilt = audio
    for before, after in P.AUDIO_TO_TRANSCRIPT:
        rebuilt = rebuilt.replace(before, after, 1)
    assert rebuilt == text, (
        "re-applying the six documented substitutions does not recover the text prompt, so "
        "the two prompts differ by something beyond them"
    )

    plan = json.loads(
        (REPO / "experiments" / "retention-e23.plan.json").read_text(encoding="utf-8"))
    assets = plan["assets"]
    assert assets["prompt_text"]["sha256"] == hashlib.sha256(
        text.encode("utf-8")).hexdigest(), "the text prompt is not what the plan pins"
    assert assets["prompt_audio"]["sha256"] == hashlib.sha256(
        audio.encode("utf-8")).hexdigest(), (
        "the audio prompt this test derives is not what the plan pins, so either the "
        "substitution set or the plan has moved"
    )


def test_the_runner_derives_the_audio_prompt_the_same_way():
    """The runner's own reversal, checked against the source rather than by importing it.

    `audio_prompt_text` must apply each pair exactly once and refuse otherwise. Asserted on
    the source because importing the runner needs httpx, which CI does not have.
    """
    assert "for before, after in P.AUDIO_TO_TRANSCRIPT:" in RAW
    assert "text.count(after) != 1" in RAW, (
        "the runner no longer refuses a substitution that does not fire exactly once; a "
        "partially reversed prompt is a prompt nobody reviewed"
    )
    assert "text.replace(after, before, 1)" in RAW
