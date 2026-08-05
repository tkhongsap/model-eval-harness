"""The retention system prompt, assembled from committed assets rather than from production.

Production builds this prompt in two halves and joins them at runtime
(`main.py:1157-1160`, and identically at `fact_checker.py:341-347`):

    system_prompt = yaml.safe_load(open('./config/system_prompt/retention.yml'))['system_prompt']
    prompt_text   = system_prompt.replace("{user_prompt}", user_prompt)

The wrapper is a checked-in YAML block scalar. The `user_prompt` is *not*: it is fetched
from SharePoint at run time (`main.py:1140-1156`), and the copy that was live when this
harness was written is the middle of `prompt.py`'s `prompt_v9_16` monolith, lines
4318-4409. Both halves are therefore extracted into `src/evalgen/prompts/` as committed
text files.

**Why they are files and not a read of `production-reference/` at runtime.** That tree
is tracked now, but reading the prompt live from it would still be wrong: this snapshot
is a deliberate, reviewed extraction, sha256-pinned in `prompts/PORT-NOTES.md`, so a
later change to the reference tree is a diff a reviewer sees rather than a prompt that
silently changes underneath a scheduled run. The assets carry the prompt; the
production tree is only ever the provenance.

Three transcription bugs were fixed on the way in. Each is recorded with before/after in
`prompts/PORT-NOTES.md`, and all three are invisible in production for the same reason:
Vertex is called with forced function calling (`main.py:1109-1114`,
`"functionCallingConfig": {"mode": "ANY"}`), so the model never has to obey the prompt's
worked example -- the tool schema decides the shape. This harness passes the schema via
`response_format` instead, which is advisory in a way `mode: ANY` is not, so a broken
example stops being decoration and starts being the thing a weaker model copies.

Modality
--------
Production sends audio (`main.py:1095-1100`, a `fileData` part with a GCS URI). This
harness sends a Thai transcript, so every sentence of the prompt that promises audio is a
lie the model has to reconcile. `AUDIO_TO_TRANSCRIPT` is that repair, and it is a literal
list of (before, after) pairs on purpose: a regex over "audio" would also rewrite phrases
nobody reviewed, and the one thing an eval prompt cannot afford is an edit nobody can
point at. Every pair is asserted to fire exactly once, so a pair that stops matching
because an asset changed fails the build instead of silently doing nothing.

Message layout
--------------
Production puts prompt and audio in a single `user` turn. Here the prompt is the system
message and the transcript is the user message. That is a deliberate divergence and the
only one: system/user separation is what OpenRouter's chat models expect, and mixing the
instructions into the user turn would make the two arms differ from every other chat
deployment they will be compared against. The transcript is sent verbatim, with no header
and no framing, because any word added around it is a word this harness invented and then
scored a model on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - a type name, never a runtime dependency
    from evalgen.testsets import TestItem

__all__ = [
    "AUDIO_TO_TRANSCRIPT",
    "BODY_PATH",
    "EXAMPLE_REASON_VALUES",
    "PROMPTS",
    "VARIANTS",
    "Edit",
    "Prompt",
    "PromptError",
    "Variant",
    "WRAPPER_PATH",
    "build_base",
    "build_messages",
    "build_variant",
    "get",
]

# `prompts.py` and `prompts/` are siblings, which is legal but worth knowing about: a
# directory without `__init__.py` is only a namespace portion, and CPython's path finder
# prefers a module to one, so `import evalgen.prompts` lands here. Adding an
# `__init__.py` to `prompts/` would turn it into a regular package, regular packages win,
# and this module becomes unimportable. Keep the asset directory a plain data folder.
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
WRAPPER_PATH = PROMPTS_DIR / "retention_wrapper.txt"
BODY_PATH = PROMPTS_DIR / "retention_v9_16_body.txt"

# The placeholder production substitutes (main.py:1160). Named rather than inlined so the
# test that proves it is gone has something to import.
USER_PROMPT_PLACEHOLDER = "{user_prompt}"

# The Role line, which must survive assembly exactly once. It lives in the wrapper
# (retention.yml:2) and also opens the `prompt_v9_16` monolith (prompt.py:4314), so
# substituting the whole monolith instead of its 4318-4409 body silently produces a
# prompt that introduces itself twice. That prompt still runs, still returns JSON, and
# still scores -- it is simply not the prompt production uses.
ROLE_MARKER = "**Role**: You are a call center agent"

# Audio -> transcript, as literal pairs. Each must appear exactly once in the assembled
# text (see `_substitute`). Kept phrase-length rather than word-length so the diff a
# reviewer reads is the diff the model sees.
#
# Deliberately NOT rewritten:
#   * "(To identify who is client, who is agent. ...)" -- the fixtures are speaker-tagged
#     so the instruction is redundant, but it never claims audio and dropping it would
#     change what the model is asked to do, not just the medium it is asked to do it in.
#   * The grammar. "Your will receive" is production's typo and it stays: this port fixes
#     what breaks under `response_format`, not what reads badly.
AUDIO_TO_TRANSCRIPT: tuple[tuple[str, str], ...] = (
    (
        "analyzing an audio recording of a client's phone call",
        "analyzing a transcript of a client's phone call",
    ),
    (
        "receive an audio file conversation between client and call center agent",
        "receive a transcript of a conversation between client and call center agent",
    ),
    (
        "If audio file has no conversation",
        "If the transcript has no conversation",
    ),
    (
        "must strictly adhere to the audio content",
        "must strictly adhere to the transcript content",
    ),
    (
        "not explicitly present in the source file",
        "not explicitly present in the transcript",
    ),
    # The one Thai pair. prompt.py:4369 makes `post to pre` unconditional on *hearing*
    # the switch; with no audio the condition is unsatisfiable as written, and this is
    # the rule several fixture items turn on. "หากพบในบทสนทนาว่า" keeps the conditional
    # and moves only the sense verb.
    (
        "หากได้ยินว่า",
        "หากพบในบทสนทนาว่า",
    ),
)


class PromptError(ValueError):
    """Raised when a prompt cannot be assembled from the committed assets.

    Assembly failures are loud because they are silent otherwise: a placeholder that
    did not substitute, or an audio phrase that no longer matches, leaves a prompt that
    is still a valid string and still gets sent. The run completes, the numbers print,
    and they describe a prompt nobody wrote.
    """


@dataclass(frozen=True)
class Prompt:
    """One system prompt, hashed so a result row can name the exact text it came from.

    `sha` is over `system_text`, not over the asset files, because the text is what the
    model saw. The assets are an implementation detail of how it was built; the
    substitutions are applied after they are read.
    """

    id: str
    system_text: str
    sha: str
    notes: str = ""


def _sha(text: str) -> str:
    """sha256 of the UTF-8 bytes. Explicit encoding, since the corpus is Thai."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    """Read an asset, with a failure message that names the file rather than the errno."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - packaging failure, not logic
        raise PromptError(
            f"prompt asset missing: {path}. These files are committed precisely so the "
            "prompt does not depend on reading production-reference/ live at run time."
        ) from exc


def _substitute(text: str) -> str:
    """Apply `AUDIO_TO_TRANSCRIPT`, refusing any pair that does not fire exactly once.

    Exactly once, not at-least-once: a phrase that matched twice means the assets grew a
    duplicate section, which is the same accident as the doubled Role line and just as
    quiet. Both directions are checked because both are ways for the audit list to stop
    describing the prompt it claims to describe.
    """
    for before, after in AUDIO_TO_TRANSCRIPT:
        count = text.count(before)
        if count != 1:
            raise PromptError(
                f"audio->transcript pair matched {count} times, expected exactly 1: "
                f"{before!r}. The prompt assets changed underneath AUDIO_TO_TRANSCRIPT; "
                "update the pair rather than dropping it, or the assembled prompt still "
                "promises the model an audio file it will never receive."
            )
        text = text.replace(before, after)
    return text


def build_base() -> Prompt:
    """The production prompt as this harness sends it: wrapper + v9_16 body, de-audioed.

    The join is production's own (`main.py:1160`): a single `str.replace` of
    `{user_prompt}`. Nothing is reordered and nothing is appended, so the only
    differences from what Vertex receives are the three example-JSON fixes recorded in
    PORT-NOTES.md and the substitutions above.
    """
    wrapper = _read(WRAPPER_PATH)
    body = _read(BODY_PATH).rstrip("\n")

    if USER_PROMPT_PLACEHOLDER not in wrapper:
        raise PromptError(
            f"{WRAPPER_PATH} has no {USER_PROMPT_PLACEHOLDER} placeholder, so the v9_16 "
            "body has nowhere to go and the prompt would ship without its analysis rules."
        )
    if ROLE_MARKER in body:
        raise PromptError(
            f"{BODY_PATH} contains the Role line. The body is prompt.py:4318-4409, which "
            "starts at '**Analysis Requirements**:'; if the Role line is in there, the "
            "whole prompt_v9_16 monolith was extracted instead of its middle and the "
            "assembled prompt introduces itself twice."
        )

    text = _substitute(wrapper.replace(USER_PROMPT_PLACEHOLDER, body))
    return Prompt(
        id="v9_16_base",
        system_text=text,
        sha=_sha(text),
        notes=(
            "Production retention.yml wrapper + prompt.py:4318-4409 (v9_16 body). "
            "Example JSON repaired (valid JSON, schema-declared `keyword` key, one "
            "product); audio references rewritten to transcript per AUDIO_TO_TRANSCRIPT."
        ),
    )


# --------------------------------------------------------------------------- variants


@dataclass(frozen=True)
class Edit:
    """One substitution applied to the base text, with the reason it is applied.

    `why` is a required field rather than a comment because the substitution list IS the
    experiment's design document: an ablation whose edits cannot be read next to their
    justification is an ablation whose result cannot be attributed to anything.
    """

    before: str
    after: str
    why: str


@dataclass(frozen=True)
class Variant:
    """An ablation of the base prompt, expressed as edits rather than as a forked file.

    Deliberately NOT a second copy of `retention_wrapper.txt`. Two files drift: someone
    fixes a typo in the base, the fork keeps it, and the arms now differ by the typo AND
    the ablation. A run comparing them reports one number and cannot say which change
    produced it. Edits against a single source make that impossible by construction --
    everything not named here is byte-identical to the base, and `build_variant` refuses
    an edit that does not fire exactly once, so a base that moved underneath the variant
    fails the build instead of quietly widening the diff.
    """

    id: str
    edits: tuple[Edit, ...]
    notes: str


def build_variant(variant: Variant, base: Prompt) -> Prompt:
    """Apply a variant's edits to the base text, each exactly once.

    Exactly once for the same reason `_substitute` demands it: an anchor that matched
    twice means the edit landed somewhere its author did not look at, and an anchor that
    matched zero times means the ablation silently did not happen -- which produces two
    arms on the same prompt, a comparison of nothing, and a number anyway.
    """
    text = base.system_text
    for edit in variant.edits:
        count = text.count(edit.before)
        if count != 1:
            raise PromptError(
                f"variant {variant.id}: anchor matched {count} times, expected exactly "
                f"1: {edit.before!r}. The base prompt changed underneath this edit; fix "
                "the anchor rather than dropping it, or the variant ships as a copy of "
                "the base and the experiment compares an arm against itself."
            )
        text = text.replace(edit.before, edit.after)
    if text == base.system_text:
        raise PromptError(
            f"variant {variant.id} produced text identical to {base.id}. A variant that "
            "changes nothing is two arms on one prompt."
        )
    return Prompt(id=variant.id, system_text=text, sha=_sha(text), notes=variant.notes)


# The three reason values the base's worked example fills in. Named here because two
# other things need them and neither should re-type them: the `v9_16_e1` edits below,
# and `fabrication.py`, which asks how many of a run's invented labels are one of these.
#
# `fabrication.py` must keep measuring against the BASE example's values even when
# scoring an e1 run -- e1 blanks two of these slots, so deriving the comparison set from
# the variant would report 0 example-derived labels by construction and prove nothing.
EXAMPLE_REASON_VALUES: dict[str, str] = {
    "main": "network",
    "secondary": "save cost",
    "third": "dissatisfied service",
}

# ONE change: the worked example stops filling `secondary` and `third` with real labels.
#
# MEASURED on the two 2026-08-04 runs: of the reason labels each model invented into
# secondary/third slots the ground truth leaves blank, 19 of 30 (63%) for
# `qwen/qwen3.6-27b` and 18 of 42 (42%) for `google/gemini-2.5-flash` were one of the
# example's own three values -- `save cost` alone accounts for 13 of Qwen's 30. The
# example is not being read as a shape to copy, it is being read as content to copy.
#
# `main` is left alone on purpose. It is the slot that demonstrates the required shape
# (`{"reason": ..., "keyword": ...}`), and blanking it too would test two things at once:
# "do not copy the labels" AND "here is a shape with no example of a filled reason". The
# variant has to be attributable, so it changes the two optional slots and nothing else.
#
# True already ran this exact fix on the neighbouring field. At prompt v9_3 the example's
# `keyword` values were realistic Thai customer speech and models regurgitated them
# verbatim; they were blanked to a description placeholder and the regurgitation stopped.
# The same fix was never applied to `reason`.
V9_16_E1 = Variant(
    id="v9_16_e1",
    edits=(
        Edit(
            before=(
                '"secondary": {\n'
                f'                "reason": "{EXAMPLE_REASON_VALUES["secondary"]}",'
            ),
            after='"secondary": {\n                "reason": "",',
            why=(
                "13 of Qwen's 30 invented secondary/third labels are this value. The "
                "empty string is what the prompt's own closing line already tells the "
                "model to emit for an undetermined field, so the example stops "
                "contradicting the instruction it sits under."
            ),
        ),
        Edit(
            before=(
                '"third": {\n'
                f'                "reason": "{EXAMPLE_REASON_VALUES["third"]}",'
            ),
            after='"third": {\n                "reason": "",',
            why=(
                "Gemini emits this value into 15 GT-blank slots across the run, more "
                "than any other label. Same fix, same reason, applied to the second "
                "optional slot so the example demonstrates zero filled optional "
                "reasons rather than one."
            ),
        ),
    ),
    notes=(
        "v9_16_base with the worked example's `secondary.reason` and `third.reason` "
        "blanked to \"\". Everything else -- the Role block, all analysis rules, the "
        "`main` slot, every `keyword`, the schema shape -- is byte-identical to the "
        "base. Tests the hypothesis that both models copy the example's reason labels "
        "into slots the transcript does not support."
    ),
)

VARIANTS: dict[str, Variant] = {v.id: v for v in (V9_16_E1,)}

# Built once at import: the assets do not change under a running process, and a prompt
# whose sha depends on when it was called is not a prompt anyone can cite in a report.
_BASE = build_base()

PROMPTS: dict[str, Prompt] = {
    p.id: p for p in (_BASE, *(build_variant(v, _BASE) for v in VARIANTS.values()))
}


def get(prompt_id: str) -> Prompt:
    """Look up a registered prompt, naming the alternatives when the id is unknown.

    A typo'd id must never fall back to the base. Two arms silently sharing one prompt is
    a comparison that measures nothing and reports a number anyway.
    """
    try:
        return PROMPTS[prompt_id]
    except KeyError:
        raise PromptError(
            f"unknown prompt id {prompt_id!r}; registered: {sorted(PROMPTS)}"
        ) from None


def build_messages(item: TestItem, prompt: Prompt) -> list[dict[str, str]]:
    """Two turns: the prompt as system, the transcript as user, verbatim.

    Verbatim matters more than it looks. The transcript is also where every evidence span
    in the testset is asserted to appear (`testsets.validate`), so anything this function
    prepends or reformats would make the text the model reads differ from the text the
    labels were checked against.
    """
    return [
        {"role": "system", "content": prompt.system_text},
        {"role": "user", "content": item.transcript_th},
    ]
